import hashlib
import json
from pathlib import Path

import pytest
from conftest import MODULE
from sqlalchemy import func, select

from app.collectors.base import SourceCollector
from app.core.exceptions import RepositoryCloneError
from app.models import ModuleVersion, Repository, User
from app.scanners.base import SecurityScanner
from app.seeds.collection import (
    BatchCollector,
    check_manifest,
    latest_tag,
    resolve_pin,
    summarize,
    write_reports,
)
from app.seeds.mirrors import MirrorManifest, MirrorSeeder, SeedAborted
from app.utils.subprocess import ProcessResult

APACHE = "Apache License\nVersion 2.0, January 2004\n"
GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
GOOD = {**MODULE, "LICENSE": APACHE}
ACCOUNT = {"login": "tf-collected", "name": "수집 미러"}
AWS_KEY = "AKIA" + "Z7QHB3K2" + "LMNOP4RT"


def url(name, org="org"):
    return f"https://github.com/{org}/{name}"


def sha(name):
    return hashlib.sha1(name.encode()).hexdigest()


def entry(name, commit=None, tag="v1.0.0", org="org", **extra):
    commit = commit or sha(name)
    return {"repository": f"{org}/{name}", "tag": tag, "commit": commit, **extra}


def manifest(*modules):
    return MirrorManifest.model_validate({"account": ACCOUNT, "modules": list(modules)})


class Flaky(SourceCollector):
    """Wraps the fixture collector; `plan[url]` is a list of errors raised before it succeeds."""

    def __init__(self, inner, plan):
        self.inner, self.plan, self.calls = inner, plan, []

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.calls.append(source)
        queue = self.plan.get(source, [])
        if queue:
            raise queue.pop(0)
        return self.inner.collect(source, destination, ref)


class Boom(SecurityScanner):
    name = "checkov"

    def scan(self, target_path):
        raise RuntimeError("tool said: token=hunter2")


def batch(stack, pinned, plan=None, **options):
    client = stack()
    client.github.pinned = pinned
    if plan is not None:
        client.publish.collector = Flaky(client.github, plan)
    seeder = MirrorSeeder(client.app.state.session_factory, client.publish, client.scans)
    delays = []
    options = {"min_interval": 0, "sleep": delays.append, "jitter": lambda: 1.0, **options}
    return client, BatchCollector(seeder, **options), delays


def stored_count(sessions):
    with sessions() as session:
        return session.scalar(select(func.count(ModuleVersion.id)))


def test_failures_are_isolated_with_a_reason_and_only_good_modules_are_stored(
    stack, sessions, tmp_path
):
    pinned = {
        url("fine"): {"v1.0.0": (sha("fine"), GOOD)},
        url("gpl"): {"v1.0.0": (sha("gpl"), {**MODULE, "LICENSE": GPL})},
        url("secret"): {"v1.0.0": (sha("secret"), {**GOOD, "main.tf": f'x = "{AWS_KEY}"\n'})},
        url("state"): {"v1.0.0": (sha("state"), {**GOOD, "terraform.tfstate": "{}"})},
        url("moved"): {"v1.0.0": ("f" * 40, GOOD)},
        url("empty"): {"v1.0.0": (sha("empty"), {"README.md": "# x", "LICENSE": APACHE})},
        url("last"): {"v1.0.0": (sha("last"), GOOD)},
    }
    names = ["gpl", "secret", "state", "moved", "empty", "fine", "last"]
    listed = manifest(*map(entry, names))
    client, collector, _ = batch(stack, pinned)
    records = collector.run(listed, apply=True)
    by_name = {r.name: r for r in records}
    assert {n: r.category for n, r in by_name.items()} == {
        "gpl": "license",
        "secret": "secret",
        "state": "sensitive_file",
        "moved": "pin_mismatch",
        "empty": "not_terraform",
        "fine": "ok",
        "last": "ok",  # A failure earlier in the list did not stop the batch.
    }
    assert [r.state for r in records if r.category == "ok"] == ["stored", "stored"]
    assert stored_count(sessions) == 2
    assert by_name["fine"].scan_status == "COMPLETED" and by_name["fine"].license == "Apache-2.0"

    summary = summarize(records, len(names))
    assert (summary["stored"], summary["quarantined"]) == (2, 5)
    assert summary["success_rate"] == round(2 / 7, 3)
    assert summary["by_category"]["license"] == 1
    assert summary["scan"]["completed"] + summary["scan"]["partial"] == 2
    # The report is files; it names rules and locations but never a secret value.
    json_path, md_path = write_reports(tmp_path / "out", "run", records, summary, True)
    assert json.loads(json_path.read_text(encoding="utf-8"))["summary"]["stored"] == 2
    assert "| org/gpl |" in md_path.read_text(encoding="utf-8")
    assert AWS_KEY not in json_path.read_text(encoding="utf-8") + md_path.read_text(
        encoding="utf-8"
    )


def test_a_stored_module_whose_scan_failed_is_not_counted_as_success(stack, sessions):
    pinned = {url("fine"): {"v1.0.0": (sha("fine"), GOOD)}}
    client, collector, _ = batch(stack, pinned)
    client.scans.scanner = Boom()
    (record,) = collector.run(manifest(entry("fine")), apply=True)
    assert record.scan_status == "FAILED"
    assert record.state in ("stored_scan_failed", "quarantined")
    assert record.category in ("scan_failed", "parser_failed")
    assert "hunter2" not in json.dumps(vars(record))
    summary = summarize([record], 1)
    assert summary["success_rate"] == 0.0


def test_transient_failures_are_retried_with_backoff_and_final_ones_are_not(stack, sessions):
    clone = RepositoryCloneError("Unable to clone or inspect the public GitHub repository.")
    pinned = {
        url("flaky"): {"v1.0.0": (sha("flaky"), GOOD)},
        url("down"): {"v1.0.0": (sha("down"), GOOD)},
        url("gpl"): {"v1.0.0": (sha("gpl"), {**MODULE, "LICENSE": GPL})},
        url("odd"): {"v1.0.0": (sha("odd"), GOOD)},
    }
    plan = {
        url("flaky"): [clone, clone],  # 429 twice, then fine
        url("down"): [clone] * 10,
        url("odd"): [RuntimeError("secret detail token=hunter2")],
    }
    client, collector, delays = batch(stack, pinned, plan, retries=3, backoff=2.0)
    listed = manifest(*(entry(n) for n in ("flaky", "down", "gpl", "odd")))
    records = {r.name: r for r in collector.run(listed, apply=True)}
    calls = client.publish.collector.calls
    assert records["flaky"].state == "stored" and records["flaky"].attempts == 3
    assert calls.count(url("flaky")) == 3
    assert delays[:2] == [2.0, 4.0]  # exponential; jitter is pinned to 1.0 here
    assert records["down"].category == "clone_failed" and records["down"].attempts == 4
    assert calls.count(url("down")) == 4
    assert calls.count(url("gpl")) == 1  # A license refusal is final: never retried.
    assert records["odd"].reason == "RuntimeError" and "hunter2" not in json.dumps(
        vars(records["odd"])
    )
    assert records["odd"].state == "quarantined"
    assert summarize(list(records.values()), 4)["retried"] == 2


def test_an_interrupted_run_resumes_without_redoing_finished_modules(stack, sessions, tmp_path):
    pinned = {url(n): {"v1.0.0": (sha(n), GOOD)} for n in ("one", "two", "three")}
    plan = {url("two"): [KeyboardInterrupt()]}
    checkpoint = tmp_path / "state" / "checkpoint.json"
    client, collector, _ = batch(stack, pinned, plan, checkpoint=checkpoint)
    listed = manifest(*(entry(n) for n in ("one", "two", "three")))
    with pytest.raises(KeyboardInterrupt):
        collector.run(listed, apply=True)
    assert [r["name"] for r in json.loads(checkpoint.read_text(encoding="utf-8"))["records"]] == [
        "one"
    ]
    assert not checkpoint.with_name("checkpoint.json.tmp").exists()

    first_calls = len(client.publish.collector.calls)
    seeder = MirrorSeeder(client.app.state.session_factory, client.publish, client.scans)
    again = BatchCollector(seeder, min_interval=0, checkpoint=checkpoint)
    records = again.run(listed, apply=True)
    assert [r.state for r in records] == ["stored"] * 3
    resumed = client.publish.collector.calls[first_calls:]
    assert url("one") not in resumed and set(resumed) == {url("two"), url("three")}
    assert stored_count(sessions) == 3
    # A checkpoint written by a preview must not make an apply run skip anything.
    preview = BatchCollector(seeder, min_interval=0, checkpoint=tmp_path / "p.json")
    preview.run(listed, apply=False)
    other = BatchCollector(seeder, checkpoint=tmp_path / "p.json")
    other.load(True)
    assert not other._done
    other.load(False)
    assert len(other._done) == 3


def test_preview_stores_nothing_even_with_several_workers(stack, sessions, settings):
    pinned = {url(n): {"v1.0.0": (sha(n), GOOD)} for n in "abcd"}
    client, collector, _ = batch(stack, pinned, workers=3)
    records = collector.run(manifest(*(entry(n) for n in "abcd")), apply=False)
    assert [r.state for r in records] == ["would_add"] * 4
    assert stored_count(sessions) == 0
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 0
        assert session.scalar(select(func.count(User.id))) == 0
    assert not list(Path(settings.snapshot_root).glob("*.tar.gz"))


def test_rerunning_the_same_manifest_changes_nothing(stack, sessions):
    pinned = {url("fine"): {"v1.0.0": (sha("fine"), GOOD)}}
    client, collector, _ = batch(stack, pinned)
    listed = manifest(entry("fine"))
    first = collector.run(listed, apply=True)
    seeder = MirrorSeeder(client.app.state.session_factory, client.publish, client.scans)
    second = BatchCollector(seeder, min_interval=0).run(listed, apply=True)
    assert [r.action for r in first] == ["added"] and [r.action for r in second] == ["unchanged"]
    assert second[0].state == "stored" and second[0].scan_status == "COMPLETED"
    assert stored_count(sessions) == 1


def test_the_rate_limit_spaces_module_starts():
    waits, now = [], [100.0]
    batch_ = BatchCollector(
        None, min_interval=2.0, sleep=waits.append, clock=lambda: now[0], jitter=lambda: 0.0
    )
    for _ in range(3):
        batch_._throttle()
    assert waits == [2.0, 4.0]
    now[0] += 10
    batch_._throttle()
    assert waits == [2.0, 4.0]  # Enough time has passed: no wait.


def test_manifest_checks_stop_name_collisions_and_skip_duplicates():
    with pytest.raises(SeedAborted):  # Same derived name from two organisations.
        check_manifest(manifest(entry("vpc", org="a"), entry("vpc", "b" * 40, org="b")))
    with pytest.raises(SeedAborted):
        check_manifest(manifest(entry("vpc", org="a"), entry("vpc", "b" * 40, org="A")))
    already = MirrorManifest.model_validate({"account": ACCOUNT, "modules": [entry("mirrored")]})
    kept, skipped = check_manifest(
        manifest(
            entry("mirrored"), entry("one"), entry("fork", sha("one")), entry("two", "c" * 40)
        ),
        already,
    )
    assert [m.name for m in kept] == ["one", "two"]
    assert skipped == {
        "org/mirrored": "already in the mirror manifest",
        "org/fork": "same commit as org/one",
    }


class Runner:
    def __init__(self, output):
        self.output, self.args = output, []

    def run(self, args, **kwargs):
        self.args.append(args)
        return ProcessResult(0, self.output)


def test_pins_resolve_to_the_commit_not_the_annotated_tag_object(tmp_path):
    out = f"{'1' * 40}\trefs/tags/v1.2.3\n{'2' * 40}\trefs/tags/v1.2.3^{{}}\n"
    runner = Runner(out)
    assert resolve_pin(runner, "org/repo", "v1.2.3", tmp_path) == "2" * 40
    assert runner.args[0][-3:] == [
        "https://github.com/org/repo",
        "refs/tags/v1.2.3",
        "refs/tags/v1.2.3^{}",
    ]
    assert (
        resolve_pin(Runner(f"{'3' * 40}\trefs/tags/v1.2.3\n"), "org/repo", "v1.2.3", tmp_path)
        == "3" * 40
    )
    with pytest.raises(RuntimeError):
        resolve_pin(Runner(""), "org/repo", "v1.2.3", tmp_path)
    tags = "\n".join(
        f"{'4' * 40}\trefs/tags/{t}" for t in ("v1.9.0", "v1.10.0", "v2.0.0-rc1", "v1.2")
    )
    assert latest_tag(Runner(tags), "org/repo", tmp_path) == "v1.10.0"


def test_the_committed_candidates_are_pinned_unique_and_not_already_mirrored():
    seeds = Path(__file__).parents[2] / "seeds"
    listed = MirrorManifest.model_validate_json(
        (seeds / "candidates.json").read_text(encoding="utf-8")
    )
    mirrored = MirrorManifest.model_validate_json(
        (seeds / "mirrors.json").read_text(encoding="utf-8")
    )
    assert listed.account.login != mirrored.account.login
    assert len(listed.modules) >= 40
    kept, skipped = check_manifest(listed, mirrored)  # Also raises on a repeated name.
    assert not skipped and len(kept) == len(listed.modules)
    assert not any(m.acknowledge_warnings for m in listed.modules)
