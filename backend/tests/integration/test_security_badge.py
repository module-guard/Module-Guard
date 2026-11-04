"""The same badge/API contract on SQLite and PostgreSQL, without changing shared fixtures."""

import os
from datetime import timedelta
from unittest.mock import Mock
from uuid import UUID, uuid4

import pytest
from conftest import prepare, publish, register, sign_in
from sqlalchemy import create_engine, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from app.core.database import Base
from app.models import ModuleVersion, Scan
from app.models.enums import ScanStatus
from app.schemas.social import BadgeInfo


@pytest.fixture(params=["sqlite", pytest.param("postgres", marks=pytest.mark.postgres)])
def sessions(request, sessions):
    if request.param == "sqlite":
        yield sessions
        return
    base_url = os.getenv("TEST_DATABASE_URL")
    if not base_url:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "badge_" + uuid4().hex
    admin = create_engine(base_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = make_url(base_url).update_query_dict({"options": f"-csearch_path={schema}"})
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def badge(client):
    response = client.get("/api/repositories")
    assert response.status_code == 200
    return response.json()[0]["badge"]


@pytest.mark.parametrize(
    "status,partial,critical,high",
    [
        ("COMPLETED", False, 0, 0),
        ("COMPLETED", True, 0, 0),
        ("COMPLETED", True, 1, 0),
        ("COMPLETED", True, 0, 1),
        ("COMPLETED", False, 1, 1),
        ("FAILED", True, 1, 1),
        *[
            (status.value, False, 0, 0)
            for status in ScanStatus
            if status not in {ScanStatus.COMPLETED, ScanStatus.FAILED}
        ],
    ],
)
def test_badge_status_and_existing_counts(stack, sessions, status, partial, critical, high):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client, "demo")
    with sessions() as session:
        scan = session.get(Scan, UUID(created["scan_id"]))
        scan.status, scan.partial_result = ScanStatus(status), partial
        version = session.get(ModuleVersion, scan.module_version_id)
        version.critical_rules, version.high_rules = critical, high
        session.commit()
    actual = badge(client)
    assert actual == {
        "version": "v1",
        "critical_rules": critical,
        "high_rules": high,
        "unknown": 1,
        "total": 3,
        "scanned_at": actual["scanned_at"],
        "scan_status": status,
        "partial_result": partial,
        "scan_id": created["scan_id"],
    }
    assert actual["scanned_at"]
    assert client.get(f"/api/scans/{actual['scan_id']}").status_code == 200


def test_no_scan_version_does_not_borrow_another_versions_result(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client, "demo")
    with sessions() as session:
        original = session.get(Scan, UUID(created["scan_id"]))
        original.partial_result = True
        version = session.get(ModuleVersion, original.module_version_id)
        session.add(
            ModuleVersion(
                repository_id=version.repository_id,
                version="v2",
                snapshot_sha256=version.snapshot_sha256,
                created_at=version.created_at + timedelta(seconds=1),
            )
        )
        session.commit()
    actual = badge(client)
    assert actual["version"] == "v2"
    assert actual["scan_status"] is None and actual["scan_id"] is None
    assert actual["partial_result"] is False and actual["scanned_at"] is None
    page = client.get("/api/repositories/alice/demo?version=v2").json()
    assert page["version"]["scan"] is None
    with sessions() as session:
        # A historical version with no scan remains unknown even with a newer scanned version.
        original = session.get(Scan, UUID(created["scan_id"]))
        old = session.get(ModuleVersion, original.module_version_id)
        new = session.scalar(select(ModuleVersion).where(ModuleVersion.version == "v2"))
        old.scan_id = None
        original.module_version_id = new.id
        new.scan_id = original.id
        session.commit()
    assert client.get("/api/repositories/alice/demo?version=v1").json()["version"]["scan"] is None
    assert badge(client)["scan_id"] == created["scan_id"]


def test_legacy_reverse_link_and_shared_fork_visibility(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client, "demo")
    with sessions() as session:
        scan = session.get(Scan, UUID(created["scan_id"]))
        scan.partial_result = True
        session.get(ModuleVersion, scan.module_version_id).scan_id = None
        session.commit()
    assert badge(client)["scan_id"] == created["scan_id"]
    sign_in(client, sessions, "bob", 2)
    assert client.post("/api/repositories/alice/demo/fork", json={}).status_code == 201
    sign_in(client, sessions)
    assert client.patch("/api/repositories/demo", json={"visibility": "PRIVATE"}).status_code == 204
    client.cookies.clear()
    assert client.get("/api/repositories").json() == []
    assert client.get("/api/users/bob/repositories?kind=forked").json() == []
    assert client.get(f"/api/scans/{created['scan_id']}").status_code == 404
    sign_in(client, sessions, "bob", 2)
    actual = client.get("/api/users/bob/repositories?kind=forked").json()[0]["badge"]
    assert actual["scan_status"] == "COMPLETED" and actual["partial_result"] is True
    assert actual["scan_id"] == created["scan_id"]
    assert client.get(f"/api/scans/{actual['scan_id']}").status_code == 200
    assert client.patch("/api/repositories/demo", json={"visibility": "PUBLIC"}).status_code == 204
    client.cookies.clear()
    assert badge(client) == actual
    assert client.get(f"/api/scans/{actual['scan_id']}").status_code == 200


def test_badge_legacy_payload_defaults():
    actual = BadgeInfo(
        version="v1", critical_rules=0, high_rules=0, unknown=0, total=0, scanned_at=None
    )
    assert actual.scan_status is None and actual.scan_id is None
    assert actual.partial_result is False


def test_unpublished_failed_attempt_is_owner_only_and_linked(stack, sessions):
    client = stack(dispatcher=Mock())
    sign_in(client, sessions)
    created = register(client, prepare(client)["upload_id"], "failed").json()
    with sessions() as session:
        scan = session.get(Scan, UUID(created["scan_id"]))
        scan.status = ScanStatus.FAILED
        scan.partial_result = True
        session.commit()
    actual = client.get("/api/users/alice/repositories").json()[0]
    assert actual["state"] == "FAILED"
    assert actual["badge"]["scan_status"] == "FAILED"
    assert actual["badge"]["partial_result"] is True
    assert actual["badge"]["scan_id"] == created["scan_id"]
    assert client.get(f"/api/scans/{created['scan_id']}").status_code == 200
    client.cookies.clear()
    assert client.get("/api/users/alice/repositories").json() == []
    assert client.get(f"/api/scans/{created['scan_id']}").status_code == 404
