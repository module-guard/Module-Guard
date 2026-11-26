import hashlib
import json

import httpx
import pytest
from conftest import MODULE, FixtureScanner, RepoCollector
from sqlalchemy import func, select

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.github import GitHubCollector
from app.collectors.router import SourceRouter, default_router
from app.collectors.terraform_registry import TerraformRegistryCollector
from app.core.exceptions import (
    SourceBlockedError,
    UnsupportedSourceError,
)
from app.models import ModuleVersion, Repository, Scan, User
from app.models.enums import ScanStatus, SourceType
from app.seeds.collection import BatchCollector
from app.seeds.mirrors import MirrorManifest, MirrorSeeder
from app.services import allowblock_service as policy
from app.services.scan_service import ScanService
from app.utils.subprocess import ProcessResult

GITHUB = "https://github.com/evil-org/bad-module"


class Deny:
    """A guard that blocks everything under one owner and remembers what it was asked."""

    def __init__(self, owner="evil-org"):
        self.owner, self.asked = owner, []

    def __call__(self, source, repository_id=None):
        self.asked.append(source)
        if f"github.com/{self.owner}/" in source.lower():
            raise SourceBlockedError()


class Recording(SourceCollector):
    def __init__(self):
        self.calls = []

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.calls.append(source)
        raise AssertionError("must not be reached")


class Runner:
    def __init__(self):
        self.calls = []

    def run(self, args, **kwargs):
        self.calls.append(args)
        return ProcessResult(0, "x")


def test_router_guards_before_delegating():
    inner = Recording()
    router = SourceRouter([inner], guard=Deny())
    with pytest.raises(SourceBlockedError):
        router.collect(GITHUB, None)
    assert inner.calls == []


def test_router_without_a_guard_behaves_as_before(settings):
    inner = RepoCollector({"main.tf": "x"})
    assert SourceRouter([inner]).supports("anything")
    assert default_router(settings).supports(GITHUB)  # unchanged call signature
    assert default_router(settings, guard=Deny()).guard is not None


def test_router_hands_the_source_as_given_to_the_guard(tmp_path):
    guard = Deny()
    router = SourceRouter([RepoCollector({"main.tf": "x"})], guard=guard)
    router.collect("hashicorp/consul/aws", tmp_path / "m")
    assert guard.asked == ["hashicorp/consul/aws"]  # no GitHub owner to match: it passes


def test_github_collector_guards_after_validation_and_before_git(settings, tmp_path):
    runner = Runner()
    collector = GitHubCollector(settings, runner, guard=Deny())
    with pytest.raises(SourceBlockedError):
        collector.collect(GITHUB + ".git", tmp_path / "t", "v1.0.0")
    assert runner.calls == []
    # Validation still comes first, so a malformed ref or URL is reported as itself.
    with pytest.raises(UnsupportedSourceError):
        collector.collect(GITHUB, tmp_path / "t", "main")
    with pytest.raises(UnsupportedSourceError):
        collector.collect("https://example.com/a/b", tmp_path / "t")
    assert runner.calls == []


def test_github_collector_runs_git_for_an_unblocked_source(settings, tmp_path):
    runner = Runner()
    collector = GitHubCollector(settings, runner, guard=Deny())
    try:
        collector.collect("https://github.com/good-org/ok", tmp_path / "t")
    except Exception:  # the fake runner fabricates no clone; only that git was reached matters
        pass
    assert runner.calls


def registry(settings, github, guard=None):
    def handler(request):
        return httpx.Response(200, json={"source": GITHUB, "version": "1.0.0"})

    return TerraformRegistryCollector(
        settings, github=github, transport=httpx.MockTransport(handler), guard=guard
    )


class Clone(SourceCollector):
    def __init__(self):
        self.refs = []

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.refs.append(ref)
        destination.mkdir(parents=True, exist_ok=True)
        return CollectedModule(
            SourceType.GITHUB, source, destination, "bad-module", "evil-org", "main", "a" * 40
        )


def test_registry_checks_its_backing_repository_even_with_a_separate_github(settings, tmp_path):
    github = Clone()  # an injected collector that has no guard of its own
    guard = Deny()
    with pytest.raises(SourceBlockedError):
        registry(settings, github, guard).collect("evil-org/bad-module/aws", tmp_path / "t")
    assert github.refs == [] and guard.asked == [GITHUB]


def test_registry_passes_through_the_guarded_github_collector(settings, tmp_path):
    runner = Runner()
    github = GitHubCollector(settings, runner, guard=Deny())
    with pytest.raises(SourceBlockedError):  # not swallowed by the v-tag retry loop
        registry(settings, github).collect("evil-org/bad-module/aws", tmp_path / "t")
    assert runner.calls == []


def test_registry_without_a_block_still_clones(settings, tmp_path):
    github = Clone()
    collected = registry(settings, github, Deny("someone-else")).collect(
        "evil-org/bad-module/aws", tmp_path / "t"
    )
    assert collected.repository_url == GITHUB and github.refs == ["v1.0.0"]


# ------------------------------------------------------------------ ScanService


def scan_service(sessions, settings, collector=None, guard=None):
    return ScanService(sessions, settings, collector, FixtureScanner({}), guard=guard)


def test_scan_service_builds_a_guarded_default_router(sessions, settings):
    service = scan_service(sessions, settings)
    assert service.guard is not None and service.collector.guard is service.guard


def test_scan_service_guards_an_injected_collector_before_collecting(sessions, settings):
    collector = Recording()
    service = scan_service(sessions, settings, collector, guard=Deny())
    scan_id = service.create(GITHUB).scan_id
    service.run(scan_id)
    with sessions() as session:
        scan = session.scalar(select(Scan).where(Scan.id == scan_id))
        assert scan.status == ScanStatus.FAILED
        assert "정책" in scan.error_message and "evil" not in scan.error_message
    assert collector.calls == []


# ------------------------------------------------------------------ mirrors and batch

GOOD = {**MODULE, "LICENSE": "Apache License\nVersion 2.0, January 2004\n"}
ACCOUNT = {"login": "tf-guarded", "name": "guarded"}


def url(name, org="org"):
    return f"https://github.com/{org}/{name}"


def sha(name):
    return hashlib.sha1(name.encode()).hexdigest()


def entry(name, org="org", tag="v1.0.0"):
    return {"repository": f"{org}/{name}", "tag": tag, "commit": sha(name)}


def manifest(*modules):
    return MirrorManifest.model_validate({"account": ACCOUNT, "modules": list(modules)})


def pins(*names, org="org"):
    return {url(n, org): {"v1.0.0": (sha(n), GOOD)} for n in names}


class Admin:
    """Writes allow/block entries the way the admin API would."""

    def __init__(self, sessions):
        self.sessions = sessions
        with sessions() as session:
            session.add(User(login="root", github_id=900))
            session.commit()

    def add(self, kind, target, effect="BLOCK"):
        with self.sessions() as session:
            admin = session.scalar(select(User).where(User.login == "root"))
            return policy.create_entry(
                session, admin, target_kind=kind, target=target, effect=effect,
                reason="r", evidence_urls=[],
            )  # fmt: skip

    def revoke(self, found):
        with self.sessions() as session:
            admin = session.scalar(select(User).where(User.login == "root"))
            policy.revoke_entry(session, admin, found.id, reason="lifted", evidence_urls=[])


@pytest.fixture
def seeded(stack, sessions):
    def build(pinned):
        client = stack()
        client.github.pinned = pinned
        seeder = MirrorSeeder(sessions, client.publish, client.scans)
        return client, seeder, Admin(sessions)

    return build


def table_counts(sessions):
    with sessions() as session:
        return tuple(
            session.scalar(select(func.count(m.id))) for m in (User, Repository, ModuleVersion)
        )


def test_mirror_skips_a_blocked_module_without_fetching_it(seeded, sessions):
    client, seeder, admin = seeded(pins("vpc"))
    admin.add("github_owner", "org")
    (outcome,) = seeder.run(manifest(entry("vpc")), apply=True)
    assert outcome.action == "skipped" and outcome.reason.startswith("SourceBlocked:")
    assert client.github.sources == []
    assert table_counts(sessions)[1:] == (0, 0)


def test_mirror_preview_checks_policy_without_an_account_or_user_lookup(seeded, sessions):
    client, seeder, admin = seeded(pins("vpc"))
    admin.add("github_repository", url("vpc"))
    before = table_counts(sessions)
    (outcome,) = seeder.run(manifest(entry("vpc")), apply=False)
    assert outcome.action == "skipped" and outcome.reason.startswith("SourceBlocked")
    assert table_counts(sessions) == before  # a preview creates nothing, not even the account
    assert client.github.sources == []


def test_mirror_policy_runs_before_the_unchanged_shortcut(seeded, sessions):
    client, seeder, admin = seeded(pins("vpc"))
    listed = manifest(entry("vpc"))
    assert seeder.run(listed, apply=True)[0].action == "added"
    assert seeder.run(listed, apply=True)[0].action == "unchanged"
    blocked = admin.add("github_owner", "org")
    for apply in (True, False):
        (outcome,) = seeder.run(listed, apply=apply)
        assert outcome.action == "skipped" and outcome.reason.startswith("SourceBlocked")
    assert table_counts(sessions)[2] == 1  # what is stored stays (no retroactive removal)
    admin.revoke(blocked)
    assert seeder.run(listed, apply=True)[0].action == "unchanged"


def test_mirror_checks_the_stored_repository_block_too(seeded, sessions):
    client, seeder, admin = seeded(pins("vpc"))
    listed = manifest(entry("vpc"))
    seeder.run(listed, apply=True)
    with sessions() as session:
        repository_id = str(session.scalar(select(Repository.id)))
    admin.add("repository", repository_id)
    (outcome,) = seeder.run(listed, apply=True)
    assert outcome.action == "skipped" and outcome.reason.startswith("SourceBlocked")


def batch(stack, sessions, pinned, checkpoint=None):
    client = stack()
    client.github.pinned = pinned
    seeder = MirrorSeeder(sessions, client.publish, client.scans)
    delays = []
    runner = BatchCollector(
        seeder,
        min_interval=0,
        sleep=delays.append,
        jitter=lambda: 1.0,
        checkpoint=checkpoint,
    )
    return client, runner, delays, Admin(sessions)


def test_blocked_modules_are_a_final_category_not_a_retry(stack, sessions, tmp_path):
    client, runner, delays, admin = batch(stack, sessions, pins("vpc", "sg"))
    admin.add("github_repository", url("vpc"))
    records = runner.run(manifest(entry("vpc"), entry("sg")), apply=True)
    by_name = {r.name: r for r in records}
    assert (by_name["vpc"].state, by_name["vpc"].category) == ("quarantined", "blocked")
    assert by_name["vpc"].attempts == 1 and delays == []
    assert by_name["sg"].state == "stored"
    assert client.github.sources == [(url("sg"), "v1.0.0")]


def test_a_block_is_not_frozen_into_the_checkpoint_and_a_lifted_block_retries(
    stack, sessions, tmp_path
):
    checkpoint = tmp_path / "cp.json"
    client, runner, delays, admin = batch(stack, sessions, pins("vpc"), checkpoint)
    listed = manifest(entry("vpc"))
    blocked = admin.add("github_owner", "org")
    (record,) = runner.run(listed, apply=True)
    assert record.category == "blocked"
    saved = json.loads(checkpoint.read_text()) if checkpoint.exists() else {"records": []}
    assert all(r["category"] != "blocked" for r in saved["records"])

    admin.revoke(blocked)
    fresh = BatchCollector(
        runner.seeder, min_interval=0, sleep=delays.append, checkpoint=checkpoint
    )
    (again,) = fresh.run(listed, apply=True)
    assert again.state == "stored", again.reason


def test_a_stored_checkpoint_does_not_hide_a_block_added_later(stack, sessions, tmp_path):
    checkpoint = tmp_path / "cp.json"
    client, runner, delays, admin = batch(stack, sessions, pins("vpc"), checkpoint)
    listed = manifest(entry("vpc"))
    assert runner.run(listed, apply=True)[0].state == "stored"
    blocked = admin.add("github_owner", "org")
    resumed = BatchCollector(
        runner.seeder, min_interval=0, sleep=delays.append, checkpoint=checkpoint
    )
    (record,) = resumed.run(listed, apply=True)
    assert (record.state, record.category) == ("quarantined", "blocked")
    admin.revoke(blocked)
    again = BatchCollector(
        runner.seeder, min_interval=0, sleep=delays.append, checkpoint=checkpoint
    )
    assert again.run(listed, apply=True)[0].state == "stored"
