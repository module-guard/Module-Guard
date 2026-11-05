import base64
import copy
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import pytest
from conftest import publish, sign_in
from sqlalchemy import select

from app.attestations import signing
from app.models import Finding, ModuleVersion, Scan
from app.scanners.reapply import reapply
from tests.attestation_helpers import crypto_material


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    return crypto_material(tmp_path_factory)


@pytest.fixture
def signed_stack(stack, sessions, monkeypatch, material):
    signer, timestamp, base_policy = material
    policy = base_policy.model_copy(deep=True)
    monkeypatch.setattr("app.api.routes.attestations.load_policy", lambda: policy)
    real_issue = signing.issue
    monkeypatch.setattr(
        signing,
        "issue",
        lambda binding, policy: real_issue(binding, policy, signer=signer, timestamp=timestamp),
    )
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client)
    scan_id = created["scan_id"]
    with sessions() as session:
        version = session.scalar(
            select(ModuleVersion).where(ModuleVersion.scan_id == UUID(scan_id))
        )
        version_id = str(version.id)
    base = f"/api/scans/{scan_id}/attestations?version_id={version_id}"
    return client, scan_id, version_id, base, policy


def test_unconfigured_legacy_is_unsigned_and_issuance_is_unavailable(stack, sessions, monkeypatch):
    monkeypatch.delenv("MODULEGUARD_ATTESTATION_POLICY_FILE", raising=False)
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client)
    scan_id = UUID(created["scan_id"])
    with sessions() as session:
        version = session.scalar(select(ModuleVersion).where(ModuleVersion.scan_id == scan_id))
        version_id = version.id
        before = copy.deepcopy(session.get(Scan, scan_id).analysis)
    path = f"/api/scans/{scan_id}/attestations?version_id={version_id}"
    response = client.get(path)
    assert response.status_code == 200
    assert response.json()["status"] == "unsigned"
    assert response.headers["cache-control"] == "no-store"
    assert client.post(path).status_code == 503
    with sessions() as session:
        assert session.get(Scan, scan_id).analysis == before
    assert client.get(f"/api/scans/{scan_id}").status_code == 200
    assert client.get(f"/api/scans/{scan_id}/graph").status_code == 200


def issue(client, path):
    response = client.post(path)
    assert response.status_code == 201, response.text
    assert response.json()["status"] == "valid", response.text
    return response.json()


def verify_path(scan_id, version_id):
    return f"/api/scans/{scan_id}/attestations/verify?version_id={version_id}"


def test_issue_read_verify_and_preserve_original_metadata(signed_stack, sessions):
    client, scan_id, version_id, path, _ = signed_stack
    with sessions() as session:
        before = copy.deepcopy(session.get(Scan, UUID(scan_id)).analysis)
        finding = session.scalar(select(Finding).where(Finding.scan_id == UUID(scan_id)))
        finding.raw_data = {**finding.raw_data, "private": "SECRET_RAW_FINDING"}
        session.commit()
    result = issue(client, path)
    [entry] = result["entries"]
    assert entry["verification"]["purpose"] == "platform"
    assert entry["verification"]["trusted_at"]
    assert entry["verification"]["security_guarantee"] is False
    assert "SECRET_RAW_FINDING" not in str(result)
    statement = signing.strict_json(base64.b64decode(entry["proof"]["envelope"]["payload"]))
    binding = statement["predicate"]["binding"]
    assert {s["name"].split(":")[0] for s in binding["subjects"]} == {
        "manifest",
        "snapshot",
        "result",
        "zip",
    }
    assert binding["metadata_complete"] is False
    assert "parent_id" not in binding and "source_url" not in binding
    assert client.get(path).json()["status"] == "valid"
    checked = client.post(verify_path(scan_id, version_id), json=entry["proof"])
    assert checked.status_code == 200 and checked.json()["status"] == "valid"
    with sessions() as session:
        after = dict(session.get(Scan, UUID(scan_id)).analysis)
        assert after == before


def test_snapshot_byte_tampering_and_zip_tampering(signed_stack, sessions):
    from app.models import DownloadArtifact

    client, scan_id, version_id, path, _ = signed_stack
    proof = issue(client, path)["entries"][0]["proof"]
    with sessions() as session:
        version = session.get(ModuleVersion, UUID(version_id))
        file = client.scans.snapshot_store.path(version.snapshot_sha256)
        original = file.read_bytes()
        file.write_bytes(original + b"tamper")
    response = client.get(path).json()
    assert response["status"] == "invalid" and response["reasons"] == ["CONTENT_MISMATCH"]
    assert client.post(verify_path(scan_id, version_id), json=proof).json()["status"] == "invalid"
    file.write_bytes(original)
    with sessions() as session:
        artifact = session.scalar(
            select(DownloadArtifact).where(DownloadArtifact.version_id == UUID(version_id))
        )
        artifact.content += b"tamper"
        session.commit()
    assert client.get(path).json()["status"] == "invalid"


def test_reapply_changes_result_revision_without_overwriting_proof(signed_stack, sessions):
    client, scan_id, _, path, _ = signed_stack
    original = issue(client, path)["entries"][0]
    with sessions() as session:
        scan = session.get(Scan, UUID(scan_id))
        provenance = copy.deepcopy(scan.analysis["provenance"])
        finding = session.scalar(
            select(Finding).where(Finding.scan_id == scan.id, Finding.rule_id == "CKV_AWS_24")
        )
        finding.guideline = None
        session.commit()
        reapply(session, dry_run=False)
    assert client.get(path).json()["status"] == "invalid"
    revised = issue(client, path)
    assert len(revised["entries"]) == 2
    assert revised["entries"][0]["proof"] == original["proof"]
    assert revised["entries"][0]["verification"]["reasons"] == ["TARGET_MISMATCH"]
    with sessions() as session:
        assert session.get(Scan, UUID(scan_id)).analysis["provenance"] == provenance


def test_fork_has_distinct_claim_even_for_identical_source(signed_stack, sessions):
    client, scan_id, version_id, path, _ = signed_stack
    parent_proof = issue(client, path)["entries"][0]["proof"]
    sign_in(client, sessions, "bob", 2)
    forked = client.post("/api/repositories/alice/demo/fork", json={"visibility": "PUBLIC"})
    assert forked.status_code == 201, forked.text
    with sessions() as session:
        versions = list(
            session.scalars(select(ModuleVersion).where(ModuleVersion.scan_id == UUID(scan_id)))
        )
        fork_version = next(v for v in versions if str(v.id) != version_id)
        fork_id = str(fork_version.id)
    target = f"/api/scans/{scan_id}/attestations?version_id={fork_id}"
    assert client.get(target).json()["status"] == "unsigned"
    wrong = client.post(verify_path(scan_id, fork_id), json=parent_proof)
    assert wrong.json()["reasons"] == ["TARGET_MISMATCH"]
    derived = issue(client, target)["entries"][0]["proof"]
    parent_claim = signing.strict_json(base64.b64decode(parent_proof["envelope"]["payload"]))
    derived_claim = signing.strict_json(base64.b64decode(derived["envelope"]["payload"]))
    for key in ("version_id", "repository_id", "lineage_id"):
        assert (
            parent_claim["predicate"]["binding"][key] != derived_claim["predicate"]["binding"][key]
        )


def test_private_visibility_owner_origin_and_wrong_version(signed_stack, sessions):
    client, scan_id, version_id, path, _ = signed_stack
    proof = issue(client, path)["entries"][0]["proof"]
    client.headers["Origin"] = "https://not-allowed.invalid"
    assert client.post(path).status_code == 403
    sign_in(client, sessions, "bob", 2)
    assert client.post(path).status_code == 404
    sign_in(client, sessions)
    assert client.patch("/api/repositories/demo", json={"visibility": "PRIVATE"}).status_code == 204
    client.cookies.clear()
    assert client.get(path).status_code == 404
    assert client.post(verify_path(scan_id, version_id), json=proof).status_code == 404
    assert (
        client.get(f"/api/scans/{uuid4()}/attestations?version_id={version_id}").status_code == 404
    )


def test_revocation_and_policy_expiry_are_rechecked(signed_stack):
    client, _, _, path, policy = signed_stack
    issue(client, path)
    policy.signers[policy.signing_key_id].revoked = True
    assert client.get(path).json()["status"] == "revoked"
    policy.expires_at = datetime.now(UTC) - timedelta(seconds=1)
    assert client.get(path).json()["status"] == "policy-expired"


def test_noop_reapply_does_not_change_signature_binding(signed_stack, sessions):
    client, _, _, path, _ = signed_stack
    # First bring fixture-era counters and rules up to date, then attest that revision.
    with sessions() as session:
        reapply(session, dry_run=False)
    issue(client, path)
    with sessions() as session:
        reapply(session, dry_run=False)
    assert client.get(path).json()["status"] == "valid"


def test_failed_partial_scan_can_be_signed_without_claiming_success(signed_stack, sessions):
    from app.models.enums import ScanStatus

    client, scan_id, _, path, _ = signed_stack
    with sessions() as session:
        scan = session.get(Scan, UUID(scan_id))
        scan.status, scan.partial_result = ScanStatus.FAILED, True
        session.commit()
    signed = issue(client, path)
    assert signed["entries"][0]["verification"]["security_guarantee"] is False
    scan = client.get(f"/api/scans/{scan_id}").json()
    assert scan["status"] == "FAILED" and scan["partial_result"] is True


def test_missing_time_evidence_and_corrupt_store_are_not_success(signed_stack):
    import json

    client, _, version_id, path, _ = signed_stack
    issue(client, path)
    directory = client.scans.snapshot_store.root / "attestations" / version_id
    file = next(directory.glob("*.json"))
    stored = json.loads(file.read_bytes())
    stored["proof"]["timestamp"] = None
    file.write_text(json.dumps(stored))
    assert client.get(path).json()["status"] == "unknown"
    file.write_text("broken SECRET_STORE_DATA")
    response = client.get(path)
    assert response.json()["status"] == "unknown"
    assert "SECRET_STORE_DATA" not in response.text


def test_signing_outage_does_not_publish_proof_or_modify_scan(
    signed_stack, sessions, monkeypatch, caplog
):
    client, scan_id, _, path, _ = signed_stack
    with sessions() as session:
        before = copy.deepcopy(session.get(Scan, UUID(scan_id)).analysis)

    def broken(*args, **kwargs):
        raise RuntimeError("SECRET_SIGNER_ERROR")

    monkeypatch.setattr(signing, "issue", broken)
    response = client.post(path)
    assert response.status_code == 503
    assert "SECRET_SIGNER_ERROR" not in response.text + caplog.text
    assert client.get(path).json()["status"] == "unsigned"
    with sessions() as session:
        assert session.get(Scan, UUID(scan_id)).analysis == before


def test_result_change_during_signing_is_not_published(signed_stack, sessions, monkeypatch):
    client, scan_id, _, path, _ = signed_stack
    real_issue = signing.issue

    def changed(binding, policy):
        proof = real_issue(binding, policy)
        with sessions() as session:
            scan = session.get(Scan, UUID(scan_id))
            scan.partial_result = True
            session.commit()
        return proof

    monkeypatch.setattr(signing, "issue", changed)
    assert client.post(path).status_code == 409
    assert client.get(path).json()["status"] == "unsigned"
