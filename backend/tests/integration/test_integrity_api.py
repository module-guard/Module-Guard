import hashlib
import io
import zipfile
from concurrent.futures import ThreadPoolExecutor
from unittest.mock import Mock

from conftest import prepare, publish, register, sign_in
from sqlalchemy import func, select

from app.models import DownloadArtifact, ModuleVersion
from app.schemas.usage import UsageRequest

SOURCE = {
    "variables.tf": 'variable "name" {\n type = string\n default = ""\n}\n',
    "main.tf": 'resource "null_resource" "example" {}\n',
    "README.md": "hello",
}
BASE = "/api/repositories/alice/module"


def setup(stack, sessions):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    publish(client, "module", SOURCE)
    return client


def test_source_manifest_and_final_zip_are_separate(stack, sessions):
    client = setup(stack, sessions)
    info = client.get(BASE + "/integrity").json()
    assert info["integrity_status"] == "VERIFIED"
    assert info["zip_sha256"] and info["download_allowed"]
    files = client.get(BASE + "/integrity/files").json()
    assert {f["path"]: f["sha256"] for f in files} == {
        p: hashlib.sha256(s.encode()).hexdigest() for p, s in SOURCE.items()
    }
    prepared = client.post(BASE + "/download/prepare", json={}).json()
    response = client.post(BASE + "/download", json={"artifact_id": prepared["artifact_id"]})
    assert response.status_code == 200
    assert (
        hashlib.sha256(response.content).hexdigest() == prepared["zip_sha256"] == info["zip_sha256"]
    )
    assert info["manifest_sha256"] != prepared["zip_sha256"] != info["snapshot_sha256"]
    archive = zipfile.ZipFile(io.BytesIO(response.content))
    assert archive.read("modules/module/main.tf") == SOURCE["main.tf"].encode()
    assert "main.tf" in archive.namelist()
    assert client.post(BASE + "/integrity/verify").status_code == 200
    scan = client.get(f"/api/scans/{info['scan_id']}").json()
    assert scan["integrity"]["manifest_sha256"] == info["manifest_sha256"]


def test_snapshot_tampering_sticky_and_no_baseline_rewrite(stack, sessions):
    client = setup(stack, sessions)
    before = client.get(BASE + "/integrity").json()
    path = client.scans.snapshot_store.path(before["snapshot_sha256"])
    original = path.read_bytes()
    path.write_bytes(original + b"tampered")
    result = client.post(BASE + "/download", json={})
    assert result.status_code == 409
    assert result.json()["error"]["code"] == "IntegrityMismatch"
    assert "application/zip" not in result.headers["content-type"]
    path.write_bytes(original)
    assert client.post(BASE + "/integrity/verify").status_code == 409
    after = client.get(BASE + "/integrity").json()
    assert after["manifest_sha256"] == before["manifest_sha256"]
    assert after["integrity_status"] == "MISMATCH"
    assert not after["download_allowed"]


def test_corrupted_zip_blocked_without_regeneration(stack, sessions):
    client = setup(stack, sessions)
    prepared = client.post(BASE + "/download/prepare", json={}).json()
    with sessions() as session:
        artifact = session.scalar(select(DownloadArtifact))
        artifact.content = b"broken zip"
        session.commit()
    for endpoint in ("/download", "/download/prepare"):
        result = client.post(BASE + endpoint, json={})
        assert result.status_code == 409
    with sessions() as session:
        artifact = session.scalar(select(DownloadArtifact))
        assert artifact.zip_sha256 == prepared["zip_sha256"]
        assert artifact.content == b"broken zip"
        assert artifact.integrity_status == "MISMATCH"


def test_missing_baseline_unverified_and_io_error(stack, sessions):
    client = setup(stack, sessions)
    before = client.get(BASE + "/integrity").json()
    path = client.scans.snapshot_store.path(before["snapshot_sha256"])
    path.unlink()
    assert client.post(BASE + "/integrity/verify").status_code == 409
    assert client.get(BASE + "/integrity").json()["integrity_status"] == "ERROR"
    with sessions() as session:
        version = session.scalar(select(ModuleVersion))
        version.manifest = None
        version.manifest_sha256 = None
        session.commit()
    assert client.post(BASE + "/download", json={}).status_code == 409
    assert client.get(BASE + "/integrity").json()["integrity_status"] == "UNVERIFIED"


def test_prepared_tree_changed_before_analysis(stack, sessions):
    client = stack(dispatcher=Mock())
    sign_in(client, sessions, "alice", 1)
    prepared = prepare(client, SOURCE)
    created = register(client, prepared["upload_id"], "module").json()
    from uuid import UUID

    scan_id = UUID(created["scan_id"])
    tree, _ = client.scans.staging.claimed(scan_id)
    (tree / "main.tf").write_text('resource "null_resource" "changed" {}')
    scanner = Mock(wraps=client.scans.scanner)
    client.scans.scanner = scanner
    client.scans.run(scan_id)
    scanner.scan.assert_not_called()
    response = client.get(f"/api/scans/{scan_id}").json()
    assert response["status"] == "FAILED"
    assert response["integrity"]["integrity_status"] == "MISMATCH"
    assert response["integrity"]["mismatched_files"] == ["main.tf"]


def test_tree_changed_during_scan_fails_completion(stack, sessions):
    client = stack(dispatcher=Mock())
    sign_in(client, sessions, "alice", 1)
    created = register(client, prepare(client, SOURCE)["upload_id"], "module").json()
    original = client.scans.scanner.scan

    def corrupt(path):
        result = original(path)
        (path / "added.tf").write_text("# injected")
        return result

    client.scans.scanner.scan = corrupt
    from uuid import UUID

    client.scans.run(UUID(created["scan_id"]))
    response = client.get(f"/api/scans/{created['scan_id']}").json()
    assert response["status"] == "FAILED"
    assert response["integrity"]["integrity_status"] == "MISMATCH"
    assert client.post(BASE + "/download", json={}).status_code == 404


def test_concurrent_downloads_reuse_immutable_artifact(stack, sessions):
    client = setup(stack, sessions)
    service = client.app.state.usage_service

    def download(_):
        return service.prepare_download(
            None, "concurrency-test", "alice", "module", UsageRequest(values={"name": "concurrent"})
        )

    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(download, range(8)))
    assert len({r.artifact_id for r in results}) == 1
    with sessions() as session:
        assert session.scalar(select(func.count()).select_from(DownloadArtifact)) == 2


def test_private_integrity_access(stack, sessions):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    publish(client, "module", SOURCE, visibility="PRIVATE")
    client.cookies.clear()
    assert client.get(BASE + "/integrity").status_code == 404
    assert client.get(BASE + "/integrity/files").status_code == 404
    assert client.post(BASE + "/integrity/verify").status_code == 404
    assert client.post(BASE + "/download/prepare", json={}).status_code == 404


def test_historical_scan_keeps_own_baseline(stack, sessions):
    client = setup(stack, sessions)
    first = client.get(BASE + "/integrity").json()
    prepared = prepare(client, {**SOURCE, "README.md": "new version"})
    created = client.post(
        "/api/repositories/module/versions", json={"upload_id": prepared["upload_id"]}
    ).json()
    from conftest import wait_terminal

    wait_terminal(client, created["scan_id"])
    second = client.get(BASE + "/integrity").json()
    assert first["manifest_sha256"] != second["manifest_sha256"]
    assert (
        client.get(f"/api/scans/{first['scan_id']}").json()["integrity"]["manifest_sha256"]
        == first["manifest_sha256"]
    )
    assert client.get(BASE + "/integrity?version=v1").json()["version_id"] == first["version_id"]


def test_stale_success_cannot_clear_concurrent_mismatch(stack, sessions, monkeypatch):
    import pytest

    from app.services.integrity_service import verify_version
    from app.storage.integrity import IntegrityError

    client = setup(stack, sessions)
    store = client.scans.snapshot_store
    read = store.verified_bytes

    def concurrent(*args):
        data = read(*args)
        with sessions() as other:
            other.scalar(select(ModuleVersion)).integrity_status = "MISMATCH"
            other.commit()
        return data

    monkeypatch.setattr(store, "verified_bytes", concurrent)
    with sessions() as session:
        version = session.scalar(select(ModuleVersion))
        with pytest.raises(IntegrityError):
            verify_version(session, version, store)
        assert version.integrity_status == "MISMATCH"


def test_artifact_id_cannot_select_different_bytes(stack, sessions):
    from uuid import uuid4

    client = setup(stack, sessions)
    prepared = client.post(BASE + "/download/prepare", json={}).json()
    assert client.post(BASE + "/download", json={"artifact_id": str(uuid4())}).status_code == 409
    assert (
        client.post(
            BASE + "/download",
            json={"artifact_id": prepared["artifact_id"], "values": {"name": "changed"}},
        ).status_code
        == 409
    )
    assert (
        client.post(BASE + "/download", json={"artifact_id": prepared["artifact_id"]}).status_code
        == 200
    )
