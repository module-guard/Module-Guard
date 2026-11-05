from uuid import UUID

import pytest
from sqlalchemy import select

from app.attestations import signing
from app.models import Finding, ModuleVersion, ReuseRecord
from app.services import reuse_service, scan_service
from tests.attestation_helpers import crypto_material
from tests.conftest import publish, sign_in


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    return crypto_material(tmp_path_factory)


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
