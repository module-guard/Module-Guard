from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from conftest import MODULE, RepoCollector, sign_in, wait_terminal
from sqlalchemy import func, select

from app.collectors.base import CollectedModule
from app.core.exceptions import SourceBlockedError
from app.models import ModuleVersion, Repository, User
from app.models.enums import OriginType, SourceType
from app.services import allowblock_service as policy

GITHUB = "https://github.com/evil-org/bad-module"
GOOD = {**MODULE, "LICENSE": "MIT"}
FIXED = "이 소스는 정책에 따라 등록하거나 수집할 수 없습니다."


@pytest.fixture
def blocker(sessions):
    """block(kind, target, effect='BLOCK', **extra) -> entry; revoke(entry)."""
    with sessions() as session:
        admin = User(login="root", github_id=900)
        session.add(admin)
        session.commit()

    class Blocker:
        def add(self, kind, target, effect="BLOCK", **extra):
            with sessions() as session:
                admin = session.scalar(select(User).where(User.login == "root"))
                return policy.create_entry(
                    session,
                    admin,
                    target_kind=kind,
                    target=target,
                    effect=effect,
                    reason="internal reason",
                    evidence_urls=[],
                    **extra,
                )

        def revoke(self, entry):
            with sessions() as session:
                admin = session.scalar(select(User).where(User.login == "root"))
                policy.revoke_entry(session, admin, entry.id, reason="lifted", evidence_urls=[])

    return Blocker()


def slots(settings):
    root = Path(settings.upload_root)
    return [p for p in root.rglob("*") if p.is_dir() and p.name == "tree"] if root.exists() else []


def versions(sessions):
    with sessions() as session:
        return session.scalar(select(func.count(ModuleVersion.id)))


def test_blocked_github_import_is_refused_before_anything_is_fetched(
    stack, sessions, settings, blocker
):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    blocker.add("github_owner", "evil-org")
    response = client.post("/api/uploads/github", json={"url": GITHUB})
    assert response.status_code == 403
    assert response.json()["error"] == {"code": "SourceBlocked", "message": FIXED}
    assert client.github.sources == []
    assert slots(settings) == []
    # An unrelated owner is untouched.
    assert (
        client.post("/api/uploads/github", json={"url": "https://github.com/org/ok"}).status_code
        == 200
    )


def test_repository_level_block_and_expiry_on_import(stack, sessions, blocker):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    entry = blocker.add(
        "github_repository", GITHUB, expires_at=datetime.now(UTC) + timedelta(hours=1)
    )
    assert client.post("/api/uploads/github", json={"url": GITHUB + ".git"}).status_code == 403
    blocker.revoke(entry)
    assert client.post("/api/uploads/github", json={"url": GITHUB}).status_code == 200


class BackedRegistry(RepoCollector):
    """A Registry address whose backing repository is `backing`."""

    def __init__(self, backing):
        super().__init__(GOOD)
        self.backing = backing

    def collect(self, source, destination, ref=None):
        super().collect(source, destination)
        return CollectedModule(
            SourceType.TERRAFORM_REGISTRY,
            source,
            destination,
            "bad-module",
            "evil-org",
            "main",
            "c" * 40,
            repository_url=self.backing,
            source_version="1.0.0",
        )


def test_registry_import_is_checked_through_its_backing_repository(
    stack, sessions, settings, blocker
):
    client = stack()
    client.publish.collector = BackedRegistry(GITHUB)
    sign_in(client, sessions, "alice", 1)
    blocker.add("github_owner", "evil-org")
    response = client.post(
        "/api/uploads/registry", json={"address": "evil-org/bad-module/aws", "version": "1.0.0"}
    )
    assert response.status_code == 403 and response.json()["error"]["code"] == "SourceBlocked"
    assert slots(settings) == []


def test_source_that_resolves_elsewhere_is_rechecked_when_sealing(
    stack, sessions, settings, blocker
):
    """The URL asked for was fine; the one the collector reports is not."""
    client = stack()
    client.publish.collector = BackedRegistry(GITHUB)
    user_id = sign_in(client, sessions, "alice", 1)
    blocker.add("github_repository", GITHUB)
    with pytest.raises(SourceBlockedError):
        client.publish.prepare_github(user_id, "https://github.com/org/fine")
    assert client.github.sources == []
    assert slots(settings) == []


def prepared_github(client, url="https://github.com/org/demo"):
    return client.post("/api/uploads/github", json={"url": url}).json()["upload_id"]


def test_register_is_refused_when_the_source_was_blocked_after_preparing(stack, sessions, blocker):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    upload = prepared_github(client)
    entry = blocker.add("github_repository", "https://github.com/org/demo")
    refused = client.post("/api/repositories", json={"upload_id": upload, "name": "demo"})
    assert refused.status_code == 403 and refused.json()["error"]["code"] == "SourceBlocked"
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 0
    # The token was not spent: once the block is lifted the same upload registers.
    blocker.revoke(entry)
    ok = client.post("/api/repositories", json={"upload_id": upload, "name": "demo"})
    assert ok.status_code == 201, ok.text


def test_a_blocked_attempt_does_not_discard_the_existing_empty_repository(stack, sessions, blocker):
    client = stack()
    user_id = sign_in(client, sessions, "alice", 1)
    with sessions() as session:
        session.add(Repository(owner_id=user_id, name="demo", origin_type=OriginType.UPLOAD))
        session.commit()
    upload = prepared_github(client)
    blocker.add("github_owner", "org")
    assert (
        client.post("/api/repositories", json={"upload_id": upload, "name": "demo"}).status_code
        == 403
    )
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 1


def test_new_version_is_refused_for_a_blocked_repository_and_blocked_source(
    stack, sessions, blocker
):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    upload = prepared_github(client)
    assert (
        client.post("/api/repositories", json={"upload_id": upload, "name": "demo"}).status_code
        == 201
    )
    with sessions() as session:
        repository_id = str(session.scalar(select(Repository.id)))
    before = versions(sessions)
    entry = blocker.add("repository", repository_id)
    second = prepared_github(client)
    refused = client.post("/api/repositories/demo/versions", json={"upload_id": second})
    assert refused.status_code == 403
    assert versions(sessions) == before
    blocker.revoke(entry)
    # Same repository, but the origin of the next version is blocked.
    blocker.add("github_owner", "org")
    refused = client.post("/api/repositories/demo/versions", json={"upload_id": second})
    assert refused.status_code == 403 and versions(sessions) == before


def test_final_gate_stops_before_a_version_row_or_token_claim_exists(stack, sessions, blocker):
    client = stack()
    user_id = sign_in(client, sessions, "alice", 1)
    upload = prepared_github(client)
    report = client.publish.staging.peek(user_id, upload)
    with sessions() as session:
        user = session.get(User, user_id)
        repository = Repository(owner_id=user_id, name="demo", origin_type=OriginType.GITHUB)
        session.add(repository)
        session.commit()
        blocker.add("repository", str(repository.id))
        with pytest.raises(SourceBlockedError):
            client.publish._start_version(
                session, user, repository, report, "v1", upload, "checkov"
            )
        session.rollback()
    assert versions(sessions) == 0
    assert client.publish.staging.peek(user_id, upload)  # still claimable


def test_stored_versions_stay_visible_and_downloadable_after_a_block(stack, sessions, blocker):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    upload = prepared_github(client)
    created = client.post("/api/repositories", json={"upload_id": upload, "name": "demo"})
    assert created.status_code == 201
    wait_terminal(client, created.json()["scan_id"])
    blocker.add("github_owner", "org")
    assert client.get("/api/repositories/alice/demo").status_code == 200
    detail = client.get("/api/repositories/alice/demo").json()
    assert detail["latest_version"] == "v1" and detail["version"]["state"] == "STORED"


def test_allow_does_not_skip_existing_checks(stack, sessions, blocker):
    client = stack(max_upload_files=1)
    sign_in(client, sessions, "alice", 1)
    blocker.add("github_owner", "org", effect="ALLOW")
    response = client.post("/api/uploads/github", json={"url": "https://github.com/org/demo"})
    assert response.status_code == 413
    assert response.json()["error"]["code"] == "TooManyFiles"
