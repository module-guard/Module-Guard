import io
import json
import stat
import time
import zipfile
from uuid import uuid4

import pytest
from conftest import ORIGIN, FakeCheckovSecrets, sign_in
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.collectors.base import CollectedModule, SourceCollector
from app.core.exceptions import RateLimitedError
from app.main import create_app
from app.models import Module, ModuleVersion, Repository, Scan
from app.models.enums import SourceType
from app.services.publish_service import PublishService, suggest_name
from app.uploads.limits import PreflightLimiter
from app.uploads.secrets import SecretInspector
from app.uploads.staging import StagedUploadNotFoundError, StagingStore

TF = 'resource "null_resource" "a" {}\n'
AWS = "AKIA" + "Z7QHB3K2" + "LMNOP4RT"
ENTROPY = "9f86d081884c7d659a2feaa0c55ad015" + "a3bf4f1b2b0b822cd15d6c15b0f00a08"
ZIP = {"Content-Type": "application/zip"}


def zip_bytes(entries, crafted=()):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
        for info, content in crafted:
            archive.writestr(info, content)
    return buffer.getvalue()


class RepoCollector(SourceCollector):
    """Writes a fixed tree where a clone would land."""

    def __init__(self, files):
        self.files, self.sources = files, []

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.sources.append(source)
        for name, content in self.files.items():
            path = destination / name
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(content)
        return CollectedModule(
            SourceType.GITHUB, source, destination, "terraform-aws-demo", "org", "main", "c" * 40
        )


@pytest.fixture
def make_client(settings, sessions):
    opened = []

    def build(checks=(), files=None, error=None, **overrides):
        configured = settings.model_copy(update=overrides)
        service = PublishService(
            sessions,
            configured,
            collector=RepoCollector(files or {"main.tf": TF, ".git/config": "[core]"}),
            inspector=SecretInspector(configured, FakeCheckovSecrets(checks, error)),
        )
        client = TestClient(create_app(configured, sessions, publish_service=service))
        client.__enter__()
        opened.append(client)
        client.service = service
        return client

    yield build
    for client in opened:
        client.__exit__(None, None, None)


def staged_files(settings):
    root = settings.upload_root
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def assert_nothing_kept(settings, sessions):
    assert staged_files(settings) == []
    with sessions() as session:
        for model in (Repository, ModuleVersion, Scan, Module):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    assert not settings.snapshot_root.exists() or not list(settings.snapshot_root.iterdir())


def test_preparing_requires_login_and_an_allowed_origin(make_client, sessions, settings):
    client = make_client()
    body = zip_bytes({"main.tf": TF})
    for request in (
        lambda **k: client.post("/api/uploads/zip", content=body, **k),
        lambda **k: client.post("/api/uploads/github", json={"url": "https://github.com/a/b"}, **k),
    ):
        # Origin is checked before anything else; a missing session is 401.
        assert request(headers=ZIP).status_code == 403
        assert request(headers={**ZIP, "Origin": "https://evil.com"}).status_code == 403
        assert request(headers={**ZIP, "Origin": ORIGIN}).status_code == 401
    sign_in(client, sessions)
    assert client.post("/api/uploads/zip", content=body, headers=ZIP).status_code == 200
    assert (
        client.post("/api/uploads/zip", content=body, headers={"Origin": ""} | ZIP).status_code
        == 403
    )
    assert_no = client.get("/api/uploads/zip")
    assert assert_no.status_code == 405


def test_zip_is_inspected_and_held_under_a_one_time_token(make_client, sessions, settings):
    client = make_client()
    user_id = sign_in(client, sessions)
    body = zip_bytes(
        {"repo/main.tf": TF, "repo/README.md": "# x", "repo/run.bin": "x", "repo/img.png": b"x"}
    )
    response = client.post("/api/uploads/zip?filename=My%20VPC.zip", content=body, headers=ZIP)
    assert response.status_code == 200, response.text
    report = response.json()
    assert report["origin_type"] == "UPLOAD" and report["origin_url"] is None
    assert report["suggested_name"] == "my-vpc" and report["file_count"] == 2
    assert report["size_bytes"] == len(TF) + 3 and report["warnings"] == []
    assert sorted(e["path"] for e in report["excluded"]) == ["img.png", "run.bin"]
    assert report["excluded_count"] == 2 and len(report["upload_id"]) >= 32
    held = staged_files(settings)
    assert sorted(p.name for p in held) == ["README.md", "main.tf", "report.json"]
    # Held under the user's folder; the token itself is nowhere on disk; the upload is gone.
    assert all(str(user_id) in str(p) for p in held)
    assert report["upload_id"] not in "".join(str(p) for p in held)
    assert report["upload_id"] not in next(p for p in held if p.name == "report.json").read_text()
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 0


@pytest.mark.parametrize(
    "entries,code",
    [
        ({"main.tf": TF, "../evil.tf": TF}, "UnsafePath"),
        ({"main.tf": TF, "/etc/evil.tf": TF}, "UnsafePath"),
        ({"main.tf": TF, "a\\b.tf": TF}, "UnsafePath"),
        ({"main.tf": TF, "C:/evil.tf": TF}, "UnsafePath"),
        ({"main.tf": TF, "MAIN.tf": TF}, "DuplicateName"),
        ({"main.tf": TF, "terraform.tfstate": "{}"}, "SensitiveFiles"),
        ({"main.tf": TF, "prod.tfvars": "x"}, "SensitiveFiles"),
        ({"main.tf": TF, ".terraform/x": "x"}, "SensitiveFiles"),
        ({"main.tf": TF, "id_rsa": "x"}, "SensitiveFiles"),
        ({"README.md": "x"}, "NoTerraformFiles"),
        ({"main.tf": TF, "bomb.json": b"\0" * (16 * 1024**2)}, "CompressionBomb"),
    ],
)
def test_rejected_archives_leave_nothing_behind(make_client, sessions, settings, entries, code):
    client = make_client()
    sign_in(client, sessions)
    response = client.post("/api/uploads/zip", content=zip_bytes(entries), headers=ZIP)
    assert response.status_code == 422, response.text
    assert response.json()["error"]["code"] == code and response.json()["error"]["message"]
    assert_nothing_kept(settings, sessions)


def test_symlink_and_non_zip_bodies_are_rejected(make_client, sessions, settings):
    client = make_client()
    sign_in(client, sessions)
    link = zipfile.ZipInfo("link.tf")
    link.create_system = 3
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    body = zip_bytes({"main.tf": TF}, [(link, "/etc/passwd")])
    assert client.post("/api/uploads/zip", content=body, headers=ZIP).json()["error"]["code"] == (
        "SymbolicLink"
    )
    garbage = client.post("/api/uploads/zip", content=b"not a zip", headers=ZIP)
    assert garbage.json()["error"]["code"] == "InvalidArchive"
    wrong_type = client.post(
        "/api/uploads/zip",
        content=zip_bytes({"main.tf": TF}),
        headers={"Content-Type": "text/plain"},
    )
    assert wrong_type.status_code == 422
    assert_nothing_kept(settings, sessions)


def test_size_limit_applies_before_and_during_the_transfer(make_client, sessions, settings):
    client = make_client(max_upload_zip_mb=1)
    sign_in(client, sessions)
    declared = client.post(
        "/api/uploads/zip", content=b"x", headers={**ZIP, "Content-Length": str(2 * 1024**2)}
    )
    assert declared.status_code == 413 and declared.json()["error"]["code"] == "ArchiveTooLarge"

    sent = []

    def stream():
        # No Content-Length (chunked): the server must stop reading once the cap is passed.
        for _ in range(64):
            sent.append(1)
            yield b"\0" * (256 * 1024)

    streamed = client.post("/api/uploads/zip", content=stream(), headers=ZIP)
    assert streamed.status_code == 413
    assert_nothing_kept(settings, sessions)
    uncompressed = make_client(max_upload_uncompressed_mb=1)
    sign_in(uncompressed, sessions, "bob", 2)
    body = zip_bytes({"main.tf": TF, "big.json": bytes(range(256)) * 8192})
    too_big = uncompressed.post("/api/uploads/zip", content=body, headers=ZIP)
    assert too_big.status_code == 413 and too_big.json()["error"]["code"] == "UncompressedTooLarge"


def test_definite_secret_refuses_the_upload_and_keeps_nothing(
    make_client, sessions, settings, caplog
):
    import logging

    caplog.set_level(logging.DEBUG)
    client = make_client()
    sign_in(client, sessions)
    body = zip_bytes({"main.tf": f'provider "aws" {{\n  access_key = "{AWS}"\n}}\n', "ok.tf": TF})
    response = client.post("/api/uploads/zip", content=body, headers=ZIP)
    assert response.status_code == 422
    error = response.json()["error"]
    assert error["code"] == "SecretsDetected"
    assert error["details"] == [
        {
            "path": "main.tf",
            "line": 2,
            "rule": "AWS_ACCESS_KEY",
            "reason": "AWS 액세스 키",
            "preview": "AKIA••••",
        }
    ]
    assert AWS not in response.text and AWS not in caplog.text and AWS[:8] not in caplog.text
    assert_nothing_kept(settings, sessions)
    # Not even the bytes of the upload survive on disk.
    assert not any(settings.upload_root.rglob("*.zip"))


def test_entropy_findings_are_warnings_held_without_previews(make_client, sessions, settings):
    check = {
        "check_id": "CKV_SECRET_19",
        "check_name": "Hex High Entropy String",
        "file_path": "/main.tf",
        "file_line_range": [1, 2],
    }
    client = make_client(checks=[check])
    sign_in(client, sessions)
    body = zip_bytes({"main.tf": f'digest = "{ENTROPY}"\n'})
    report = client.post("/api/uploads/zip", content=body, headers=ZIP).json()
    assert report["warnings"] == [
        {
            "file": "main.tf",
            "line": 1,
            "rule": "CKV_SECRET_19",
            "title": "Hex High Entropy String",
            "preview": "9f86••••",
        }
    ]
    stored = json.loads(
        next(p for p in staged_files(settings) if p.name == "report.json").read_text()
    )
    assert stored["warnings"] == [{"file": "main.tf", "line": 1, "rule": "CKV_SECRET_19"}]
    assert "9f86" not in json.dumps(stored)


def test_secret_scan_failure_blocks_the_upload(make_client, sessions, settings):
    client = make_client(error=FileNotFoundError("checkov"))
    sign_in(client, sessions)
    response = client.post("/api/uploads/zip", content=zip_bytes({"main.tf": TF}), headers=ZIP)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "SecretScanUnavailableError"
    assert_nothing_kept(settings, sessions)


def test_github_import_follows_the_same_rules(make_client, sessions, settings):
    files = {
        "main.tf": TF,
        "README.md": "# demo",
        "scripts/x.bin": "x",
        ".git/config": "[core]",
        ".git/HEAD": "ref",
    }
    client = make_client(files=files)
    sign_in(client, sessions)
    report = client.post(
        "/api/uploads/github", json={"url": "https://github.com/Org/Terraform-AWS-Demo.git"}
    ).json()
    assert report["origin_type"] == "GITHUB" and report["commit_hash"] == "c" * 40
    assert report["origin_url"] == "https://github.com/org/terraform-aws-demo"
    assert client.service.collector.sources == ["https://github.com/org/terraform-aws-demo"]
    assert report["suggested_name"] == "terraform-aws-demo" and report["file_count"] == 2
    assert [e["path"] for e in report["excluded"]] == ["scripts/x.bin"]
    assert sorted(p.name for p in staged_files(settings)) == ["README.md", "main.tf", "report.json"]
    for bad in ("file:///etc", "https://gitlab.com/a/b", "https://token@github.com/a/b"):
        assert client.post("/api/uploads/github", json={"url": bad}).status_code == 422

    sensitive = make_client(files={**files, "env/prod.tfvars": "x"})
    sign_in(sensitive, sessions, "bob", 2)
    refused = sensitive.post("/api/uploads/github", json={"url": "https://github.com/a/b"})
    assert refused.status_code == 422 and refused.json()["error"]["code"] == "SensitiveFiles"

    leaked = make_client(files={**files, "docs/setup.md": f"export KEY={AWS}\n"})
    sign_in(leaked, sessions, "carol", 3)
    refused = leaked.post("/api/uploads/github", json={"url": "https://github.com/a/b"})
    assert refused.json()["error"]["code"] == "SecretsDetected" and AWS not in refused.text
    assert [p for p in staged_files(settings) if "carol" in str(p)] == []


def test_a_new_preparation_replaces_the_previous_one(make_client, sessions, settings):
    client = make_client()
    user_id = sign_in(client, sessions)
    first = client.post("/api/uploads/zip", content=zip_bytes({"main.tf": TF}), headers=ZIP).json()
    second = client.post("/api/uploads/zip", content=zip_bytes({"b.tf": TF}), headers=ZIP).json()
    staging = client.service.staging
    with pytest.raises(StagedUploadNotFoundError):
        staging.peek(user_id, first["upload_id"])
    assert staging.peek(user_id, second["upload_id"])["file_count"] == 1
    assert sorted(p.name for p in staged_files(settings)) == ["b.tf", "report.json"]


def test_token_is_bound_to_its_user_expires_and_works_once(tmp_path):
    store = StagingStore(tmp_path, ttl_seconds=60)
    alice, mallory, scan_id = uuid4(), uuid4(), uuid4()
    token, slot = store.open(alice)
    (slot / "tree").mkdir()
    (slot / "tree" / "main.tf").write_text(TF)
    # Not claimable until the inspection result is sealed.
    with pytest.raises(StagedUploadNotFoundError):
        store.peek(alice, token)
    store.seal(slot, {"file_count": 1})
    with pytest.raises(StagedUploadNotFoundError):
        store.claim(mallory, token, scan_id)
    with pytest.raises(StagedUploadNotFoundError):
        store.claim(alice, token + "x", scan_id)
    assert store.claim(alice, token, scan_id)["file_count"] == 1
    tree, report = store.claimed(scan_id)
    assert (tree / "main.tf").read_text() == TF and report["file_count"] == 1
    with pytest.raises(StagedUploadNotFoundError):
        store.claim(alice, token, uuid4())
    store.release(scan_id)
    assert store.claimed(scan_id) is None

    expired = StagingStore(tmp_path / "e", ttl_seconds=-1)
    token, slot = expired.open(alice)
    expired.seal(slot, {})
    with pytest.raises(StagedUploadNotFoundError, match="만료"):
        expired.claim(alice, token, uuid4())
    assert not slot.exists()


def test_purge_removes_expired_abandoned_and_stale_claimed_slots(tmp_path):
    store = StagingStore(tmp_path, ttl_seconds=60, claimed_ttl_seconds=120)
    fresh_user, old_user, dead_user, claimer = uuid4(), uuid4(), uuid4(), uuid4()
    fresh_token, fresh = store.open(fresh_user)
    store.seal(fresh, {})
    _, old = store.open(old_user)
    store.seal(old, {})
    _, dead = store.open(dead_user)  # Never sealed: the request died.
    token, slot = store.open(claimer)
    store.seal(slot, {})
    scan_id = uuid4()
    store.claim(claimer, token, scan_id)
    assert store.purge_expired() == 0
    now = time.time()
    assert store.purge_expired(now + 61) == 3  # sealed x2 and the unsealed one
    assert not old.exists() and not dead.exists() and not fresh.exists()
    assert store.claimed(scan_id) is not None
    assert store.purge_expired(now + 121) == 1
    assert store.claimed(scan_id) is None
    assert not (tmp_path / "users").exists() or not list((tmp_path / "users").iterdir())


def test_one_preparation_at_a_time_and_an_hourly_cap(make_client, sessions):
    client = make_client(upload_preflights_per_hour=3)
    user_id = sign_in(client, sessions)
    body = zip_bytes({"main.tf": TF})
    with client.service.limiter.slot(user_id):
        busy = client.post("/api/uploads/zip", content=body, headers=ZIP)
        assert busy.status_code == 429 and busy.json()["error"]["code"] == "RateLimitedError"
        assert (
            client.post("/api/uploads/github", json={"url": "https://github.com/a/b"}).status_code
            == 429
        )
    # The held slot counted as one; two more are allowed, including a rejected one.
    assert client.post("/api/uploads/zip", content=body, headers=ZIP).status_code == 200
    assert client.post("/api/uploads/zip", content=b"junk", headers=ZIP).status_code == 422
    assert client.post("/api/uploads/zip", content=body, headers=ZIP).status_code == 429
    other = sign_in(client, sessions, "bob", 2)
    assert other != user_id
    assert client.post("/api/uploads/zip", content=body, headers=ZIP).status_code == 200


def test_limiter_window_slides(monkeypatch):
    clock = [0.0]
    limiter = PreflightLimiter(2, clock=lambda: clock[0])
    user = uuid4()
    for _ in range(2):
        with limiter.slot(user):
            pass
    with pytest.raises(RateLimitedError):
        with limiter.slot(user):
            pass
    clock[0] = 3601
    with limiter.slot(user):
        with pytest.raises(RateLimitedError):
            with limiter.slot(user):
                pass


def test_suggested_names_are_slugs():
    assert suggest_name("Terraform AWS VPC.zip") == "terraform-aws-vpc"
    assert suggest_name("../../etc/passwd") == "passwd"
    assert suggest_name("한글.zip") == "module" and suggest_name(None) == "module"
    assert len(suggest_name("a" * 300)) == 100
