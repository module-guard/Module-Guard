import json
from pathlib import Path

import pytest
from conftest import ENTROPY, ENTROPY_CHECK, MODULE, sign_in
from sqlalchemy import func, select

from app.models import ModuleVersion, Repository, Scan, User
from app.seeds.mirrors import MirrorManifest, MirrorSeeder, SeedAborted

APACHE = "Apache License\nVersion 2.0, January 2004\n"
GPL = "GNU GENERAL PUBLIC LICENSE\nVersion 3, 29 June 2007\n"
GOOD = {**MODULE, "LICENSE": APACHE}
ACCOUNT = {"login": "tf-mirror", "name": "tf 미러"}
AWS_KEY = "AKIA" + "Z7QHB3K2" + "LMNOP4RT"


def url(name):
    return f"https://github.com/org/{name}"


def entry(name, commit="a" * 40, tag="v1.0.0", **extra):
    return {"repository": f"org/{name}", "tag": tag, "commit": commit, **extra}


def manifest(*modules):
    return MirrorManifest.model_validate({"account": ACCOUNT, "modules": list(modules)})


def seeded(stack, pinned, **options):
    client = stack(**options)
    client.github.pinned = pinned
    return client, MirrorSeeder(client.app.state.session_factory, client.publish, client.scans)


def counts(sessions):
    with sessions() as session:
        return tuple(
            session.scalar(select(func.count(model.id)))
            for model in (User, Repository, ModuleVersion, Scan)
        )


def test_preview_stores_nothing_and_apply_is_idempotent(stack, sessions, settings):
    pinned = {url("terraform-aws-vpc"): {"v1.0.0": ("a" * 40, GOOD)}}
    client, seeder = seeded(stack, pinned)
    listed = manifest(entry("terraform-aws-vpc", description="Network"))

    (preview,) = seeder.run(listed, apply=False)
    assert (preview.action, preview.license) == ("add", "Apache-2.0")
    assert counts(sessions) == (0, 0, 0, 0)
    assert not list(Path(settings.snapshot_root).glob("*.tar.gz"))

    (first,) = seeder.run(listed, apply=True)
    assert first.action == "added", first.reason
    assert client.github.sources[-1] == (url("terraform-aws-vpc"), "v1.0.0")
    assert counts(sessions) == (1, 1, 1, 1)
    for _ in range(2):
        (again,) = seeder.run(listed, apply=True)
        assert again.action == "unchanged"
    assert seeder.run(listed, apply=False)[0].action == "unchanged"
    assert counts(sessions) == (1, 1, 1, 1)
    assert len(list(Path(settings.snapshot_root).glob("*.tar.gz"))) == 1

    # It is an ordinary public module of an account nobody can sign in to.
    page = client.get("/api/repositories/tf-mirror/terraform-aws-vpc").json()
    assert page["mirror"] is True and page["origin_url"] == url("terraform-aws-vpc")
    assert page["description"] == "Network" and page["visibility"] == "PUBLIC"
    assert (page["star_count"], page["fork_count"]) == (0, 0)
    version = page["version"]
    assert (version["license"], version["source_ref"]) == ("Apache-2.0", "v1.0.0")
    assert version["state"] == "STORED" and version["scan"]["status"] == "COMPLETED"
    card = client.get("/api/repositories").json()[0]
    assert card["mirror"] is True and (card["star_count"], card["fork_count"]) == (0, 0)
    profile = client.get("/api/users/TF-Mirror").json()
    assert profile["mirror"] is True and profile["name"] == "tf 미러"
    with sessions() as session:
        user = session.scalar(select(User))
        assert user.is_system and user.github_id is None

    # Anyone can still fork it; only then does the real count move.
    sign_in(client, sessions, "alice", 1)
    assert (
        client.post("/api/repositories/tf-mirror/terraform-aws-vpc/fork", json={}).status_code
        == 201
    )
    assert client.get("/api/repositories/alice/terraform-aws-vpc").json()["mirror"] is False
    assert client.get("/api/repositories/tf-mirror/terraform-aws-vpc").json()["fork_count"] == 1


def test_a_bumped_pin_adds_the_next_version(stack, sessions):
    newer = {**GOOD, "README.md": "# newer"}
    pinned = {url("kms"): {"v1.0.0": ("a" * 40, GOOD), "v1.1.0": ("b" * 40, newer)}}
    client, seeder = seeded(stack, pinned)
    assert seeder.run(manifest(entry("kms")), apply=True)[0].action == "added"
    bumped = manifest(entry("kms", "b" * 40, "v1.1.0"))
    assert seeder.run(bumped, apply=False)[0].action == "update"
    (updated,) = seeder.run(bumped, apply=True)
    assert updated.action == "updated" and "v2" in updated.notes[-1]
    assert seeder.run(bumped, apply=True)[0].action == "unchanged"
    page = client.get("/api/repositories/tf-mirror/kms").json()
    assert page["latest_version"] == "v2" and page["version"]["source_ref"] == "v1.1.0"
    assert [v["version"] for v in page["versions"]] == ["v2", "v1"]
    assert counts(sessions) == (1, 1, 2, 2)


def test_modules_that_break_a_rule_are_left_out_with_the_reason(stack, sessions, settings):
    pinned = {
        url("no-license"): {"v1.0.0": ("a" * 40, MODULE)},
        url("gpl"): {"v1.0.0": ("a" * 40, {**MODULE, "LICENSE": GPL})},
        url("secret"): {"v1.0.0": ("a" * 40, {**GOOD, "main.tf": f'x = "{AWS_KEY}"\n'})},
        url("state"): {"v1.0.0": ("a" * 40, {**GOOD, "terraform.tfstate": "{}"})},
        url("moved"): {"v1.0.0": ("f" * 40, GOOD)},
        url("fine"): {"v1.0.0": ("a" * 40, GOOD)},
    }
    client, seeder = seeded(stack, pinned)
    names = ["no-license", "gpl", "secret", "state", "moved", "fine"]
    for apply in (False, True):
        results = {o.name: o for o in seeder.run(manifest(*map(entry, names)), apply=apply)}
        assert {n: o.action for n, o in results.items() if n != "fine"} == dict.fromkeys(
            names[:-1], "skipped"
        )
        assert "no recognised LICENSE" in results["no-license"].reason
        assert "license" in results["gpl"].reason
        assert results["secret"].reason.startswith("SecretsDetected")
        assert AWS_KEY not in json.dumps([vars(o) for o in results.values()])
        assert results["state"].reason.startswith("SensitiveFile")
        assert results["moved"].reason.startswith("PinnedCommitMismatch")
        assert results["fine"].action == ("added" if apply else "add")
    # Only the accepted module left anything behind.
    assert counts(sessions) == (1, 1, 1, 1)
    assert len(list(Path(settings.snapshot_root).glob("*.tar.gz"))) == 1
    assert not any(Path(settings.upload_root).rglob("main.tf"))


def test_possible_secrets_are_never_acknowledged_automatically(stack, sessions):
    tree = {**GOOD, "main.tf": f'x = "{ENTROPY}"\n'}
    client, seeder = seeded(
        stack, {url("entropy"): {"v1.0.0": ("a" * 40, tree)}}, checks=[ENTROPY_CHECK]
    )
    for apply in (False, True):
        (held,) = seeder.run(manifest(entry("entropy")), apply=apply)
        assert held.action == "skipped" and "need review" in held.reason
        assert held.notes[-1].startswith("warning main.tf:")
        assert ENTROPY not in json.dumps(vars(held))
    assert counts(sessions) == (1, 0, 0, 0)  # The account exists; nothing was published.
    # After a person has looked at it, the warning can be acknowledged in the manifest.
    reviewed = manifest(entry("entropy", acknowledge_warnings=True, reviewed="test value"))
    assert seeder.run(reviewed, apply=False)[0].action == "add"
    assert seeder.run(reviewed, apply=True)[0].action == "added"


def test_the_account_name_must_not_belong_to_a_person(stack, sessions):
    client, seeder = seeded(stack, {url("vpc"): {"v1.0.0": ("a" * 40, GOOD)}})
    with sessions() as session:
        session.add(User(github_id=9, login="TF-Mirror"))
        session.commit()
    for apply in (False, True):
        with pytest.raises(SeedAborted):
            seeder.run(manifest(entry("vpc")), apply=apply)
    assert counts(sessions) == (1, 0, 0, 0)
    assert not client.github.sources


def test_the_committed_manifest_is_valid_and_pinned():
    path = Path(__file__).parents[2] / "seeds/mirrors.json"
    listed = MirrorManifest.model_validate_json(path.read_text(encoding="utf-8"))
    assert listed.account.login == "terraform-aws-modules-mirror"
    assert 12 <= len(listed.modules) <= 15
    assert len({m.name for m in listed.modules}) == len(listed.modules)
    assert all(m.repository.startswith("terraform-aws-modules/") for m in listed.modules)
    # Every acknowledgement carries the note of the person who checked it.
    assert [m.name for m in listed.modules if m.acknowledge_warnings] == ["terraform-aws-rds"]
    assert all(m.reviewed for m in listed.modules if m.acknowledge_warnings)
    with pytest.raises(ValueError):
        manifest(entry("vpc", acknowledge_warnings=True))
    for bad in ({"tag": "main"}, {"commit": "abc"}, {"stars": 100}, {"repository": "x"}):
        with pytest.raises(ValueError):
            manifest({**entry("vpc"), **bad})
