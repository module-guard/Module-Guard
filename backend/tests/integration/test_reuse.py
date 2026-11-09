from concurrent.futures import ThreadPoolExecutor
from threading import Event
from uuid import UUID

import pytest
from sqlalchemy import func, select

from app.attestations import signing
from app.models import Finding, GraphEdge, ModuleVersion, ParsedEntity, ReuseRecord, Scan
from app.models.enums import ScanStatus
from app.services import reuse_service, scan_service
from tests.attestation_helpers import crypto_material
from tests.conftest import prepare, publish, register, sign_in


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    return crypto_material(tmp_path_factory)


@pytest.fixture
def trusted_source(stack, sessions, monkeypatch, material):
    signer, timestamp, base = material
    policy = base.model_copy(deep=True)
    monkeypatch.setattr(reuse_service, "load_policy", lambda: policy)
    monkeypatch.setattr("app.api.routes.attestations.load_policy", lambda: policy)
    real_issue = signing.issue
    monkeypatch.setattr(
        signing, "issue", lambda b, p: real_issue(b, p, signer=signer, timestamp=timestamp)
    )
    capture = scan_service.capture_provenance
    monkeypatch.setattr(
        scan_service,
        "capture_provenance",
        lambda *args: {
            **capture(*args),
            "checkov_version": "test-fixture-1",
            "moduleguard_version": "test",
        },
    )
    client = stack()
    sign_in(client, sessions)
    first, _ = publish(client, name="original", visibility="PRIVATE")
    with sessions() as session:
        version = session.scalar(
            select(ModuleVersion).where(ModuleVersion.scan_id == UUID(first["scan_id"]))
        )
        version_id = version.id
    proof = client.post(
        f"/api/scans/{first['scan_id']}/attestations", params={"version_id": str(version_id)}
    )
    assert proof.status_code == 201 and proof.json()["status"] == "valid", proof.text
    # Hold the target in QUEUED so each scenario can drive the real worker itself.
    monkeypatch.setattr(client.app.state.dispatcher, "submit", lambda scan_id: None)
    target = register(client, prepare(client)["upload_id"], name="copy", visibility="PRIVATE")
    assert target.status_code == 201, target.text
    return client, UUID(first["scan_id"]), UUID(target.json()["scan_id"])


def test_concurrent_duplicate_target_commits_once(trusted_source, sessions, monkeypatch):
    client, source_id, target_id = trusted_source
    entered, release = Event(), Event()
    copies = []
    original_copy = reuse_service.copy_rows

    def copy_once(session, model, *args):
        if model is ParsedEntity:
            copies.append(model)
            entered.set()
            assert release.wait(10), "Timed out waiting for duplicate worker"
        return original_copy(session, model, *args)

    monkeypatch.setattr(reuse_service, "copy_rows", copy_once)
    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(client.scans.run, target_id)
        try:
            assert entered.wait(10), "Target never reached verified reuse"
            # The first worker still owns the target: the duplicate must leave it alone.
            pool.submit(client.scans.run, target_id).result(timeout=10)
        finally:
            release.set()
        first.result(timeout=10)
    assert len(copies) == 1
    with sessions() as session:
        scan = session.get(Scan, target_id)
        assert scan.status == ScanStatus.COMPLETED
        decisions = list(
            session.scalars(select(ReuseRecord).where(ReuseRecord.target_scan_id == target_id))
        )
        assert len(decisions) == 1 and decisions[0].reused
        assert decisions[0].source_scan_id == source_id
        for model in (ParsedEntity, GraphEdge, Finding):
            source_count = session.scalar(
                select(func.count()).select_from(model).where(model.scan_id == source_id)
            )
            copied_count = session.scalar(
                select(func.count()).select_from(model).where(model.scan_id == target_id)
            )
            assert copied_count == source_count > 0
        entity_ids = set(
            session.scalars(select(ParsedEntity.id).where(ParsedEntity.scan_id == target_id))
        )
        for edge in session.scalars(select(GraphEdge).where(GraphEdge.scan_id == target_id)):
            assert edge.source_entity_id in entity_ids
            assert edge.target_entity_id is None or edge.target_entity_id in entity_ids


def test_copy_failure_rolls_back_results(trusted_source, sessions, monkeypatch):
    client, source_id, target_id = trusted_source
    original_copy = reuse_service.copy_rows
    flushed = []

    def interrupted_copy(session, model, *args):
        if model is Finding:
            raise RuntimeError("Injected copy failure")
        original_copy(session, model, *args)
        flushed.append(model)

    monkeypatch.setattr(reuse_service, "copy_rows", interrupted_copy)
    client.scans.run(target_id)
    assert flushed == [ParsedEntity, GraphEdge]
    with sessions() as session:
        assert session.get(Scan, target_id).status == ScanStatus.FAILED
        assert session.get(Scan, source_id).status == ScanStatus.COMPLETED
        assert (
            session.scalar(
                select(func.count())
                .select_from(ReuseRecord)
                .where(ReuseRecord.target_scan_id == target_id)
            )
            == 0
        )
        for model in (ParsedEntity, GraphEdge, Finding):
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.scan_id == target_id)
                )
                == 0
            )
            assert (
                session.scalar(
                    select(func.count()).select_from(model).where(model.scan_id == source_id)
                )
                > 0
            )


def test_signed_reuse_and_revocation(stack, sessions, monkeypatch, material):
    signer, timestamp, base = material
    policy = base.model_copy(deep=True)
    monkeypatch.setattr(reuse_service, "load_policy", lambda: policy)
    monkeypatch.setattr("app.api.routes.attestations.load_policy", lambda: policy)
    real_issue = signing.issue
    monkeypatch.setattr(
        signing, "issue", lambda b, p: real_issue(b, p, signer=signer, timestamp=timestamp)
    )
    capture = scan_service.capture_provenance

    def fixture_provenance(*args):
        return {
            **capture(*args),
            "checkov_version": "test-fixture-1",
            "moduleguard_version": "test",
        }

    monkeypatch.setattr(scan_service, "capture_provenance", fixture_provenance)
    client = stack()
    sign_in(client, sessions)
    first, _ = publish(client, name="original", visibility="PRIVATE")
    with sessions() as session:
        v = session.scalar(
            select(ModuleVersion).where(ModuleVersion.scan_id == UUID(first["scan_id"]))
        )
        vid = v.id
    proof = client.post(
        f"/api/scans/{first['scan_id']}/attestations", params={"version_id": str(vid)}
    )
    assert proof.status_code == 201, proof.text
    assert proof.json()["status"] == "valid"
    second, scan = publish(client, name="copy", visibility="PRIVATE")
    assert scan["status"] == "COMPLETED"
    with sessions() as session:
        record = session.scalar(
            select(ReuseRecord).where(ReuseRecord.target_scan_id == UUID(second["scan_id"]))
        )
        assert record.reused
        assert str(record.source_scan_id) == first["scan_id"]
        assert (
            session.scalar(select(Finding).where(Finding.scan_id == UUID(second["scan_id"])))
            is not None
        )
        record.reason = "rewrite"
        with pytest.raises(ValueError, match="immutable"):
            session.commit()
        session.rollback()
    # Re-evaluated trust, never a remembered valid flag.
    for key in policy.signers:
        policy.signers[key].revoked = True
    third, _ = publish(client, name="revoked")
    with sessions() as session:
        record = session.scalar(
            select(ReuseRecord).where(ReuseRecord.target_scan_id == UUID(third["scan_id"]))
        )
        assert not record.reused
    sign_in(client, sessions, "bob", 2)
    assert client.get("/api/repositories/alice/copy/trust/reuse").status_code == 404


def test_missing_conditions_never_reuses(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    first, _ = publish(client)
    r = client.get("/api/repositories/alice/demo/trust/reuse")
    assert r.status_code == 200
    row = r.json()["records"][0]
    assert row["target_scan_id"] == first["scan_id"]
    assert not row["reused"]
    assert row["source_scan_id"] is None
    assert row["measured_saving_ms"] is None
