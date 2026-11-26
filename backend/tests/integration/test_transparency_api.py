import json
from uuid import UUID, uuid4

import pytest
from conftest import publish, sign_in
from sqlalchemy import select

from app.models import Finding, ModuleVersion, Scan
from app.models.enums import ScanStatus
from app.services.attestation_service import AttestationService, result_export_digest
from app.transparency import log as tlog
from app.transparency import merkle
from app.transparency.cli import main


@pytest.fixture
def published(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client)
    scan_id = UUID(created["scan_id"])
    with sessions() as session:
        version = session.scalar(select(ModuleVersion).where(ModuleVersion.scan_id == scan_id))
        version_id = version.id
    return client, scan_id, version_id


def test_result_export_digest_is_the_result_subject_of_the_attestation_binding(published, sessions):
    client, scan_id, version_id = published
    with sessions() as session:
        service = AttestationService(session, client.scans.snapshot_store)
        scan, version = service.target(scan_id, version_id, None)
        subjects = service.binding(scan, version)["subjects"]
        [result] = [s for s in subjects if s["name"] == f"result:{scan_id}"]
        assert result_export_digest(session, scan) == result["digest"]["sha256"]


# ----------------------------------------------------------------- command line


@pytest.fixture
def run(published, settings, tmp_path, capsys):
    log_dir = tmp_path / "transparency"

    def call(*args, database=True, log=True):
        extra = []
        if database:
            extra += ["--database-url", settings.database_url]
        if log:
            extra += ["--log-dir", str(log_dir)]
        code = main([*args, *extra])
        return code, capsys.readouterr().out

    call.dir = log_dir
    return call


def edit_finding(sessions, scan_id, text):
    with sessions() as session:
        finding = session.scalar(select(Finding).where(Finding.scan_id == scan_id))
        finding.guideline = text
        session.commit()


def verify_json(run, *args):
    code, out = run("verify", "--json", *args)
    return code, json.loads(out)


def tamper(path):
    path.write_bytes(path.read_bytes().replace(b'"COMPLETED"', b'"FAILED"   '))


def commitment(i):
    return {
        "scan_id": str(UUID(int=i + 1)),
        "module_version_id": None,
        "status": "COMPLETED",
        "result_sha256": f"{i:064x}",
        "manifest_sha256": None,
        "snapshot_sha256": None,
    }


def test_append_checkpoint_verify_tamper_revise_verify(published, sessions, run):
    _, scan_id, _ = published
    assert run("append")[0] == 0
    assert run("append")[1].startswith("appended=0")  # Idempotent.
    code, out = run("checkpoint")
    assert code == 0 and "checkpoint created" in out
    assert "checkpoint unchanged" in run("checkpoint")[1]
    code, report = verify_json(run)
    assert code == 0 and report["status"] == "ok" and report["findings"] == []
    assert report["scans"]["ok"] == 1 and report["log"]["tree_size"] == 1

    edit_finding(sessions, scan_id, "changed behind the log's back")
    code, report = verify_json(run)
    assert code == 1 and report["findings"] == ["MISMATCH"]
    assert report["scans"]["mismatch"] == [str(scan_id)]
    assert run("verify")[0] == 1

    before = (run.dir / "leaves.jsonl").read_bytes()
    code, out = run("record-revisions", "--reason", "reapply 2026-11-26")
    assert code == 0 and f"would record revision {scan_id}" in out and "Dry run" in out
    assert (run.dir / "leaves.jsonl").read_bytes() == before  # Dry run wrote nothing.
    assert verify_json(run)[0] == 1
    other = ["--apply", "--scan-id", str(uuid4())]
    assert run("record-revisions", "--reason", "reapply", *other)[1].startswith("Recorded 0")

    code, out = run("record-revisions", "--reason", "reapply 2026-11-26", "--apply")
    assert code == 0 and f"revision {scan_id}" in out
    code, report = verify_json(run)
    assert code == 0 and report["scans"]["pending"] == 1  # Declared, not yet in a checkpoint.
    assert run("checkpoint")[0] == 0
    code, report = verify_json(run)
    assert code == 0 and report["scans"]["ok"] == 1 and report["log"]["tree_size"] == 2
    lines = (run.dir / "leaves.jsonl").read_text().split("\n")[:-1]
    assert [json.loads(line)["kind"] for line in lines] == ["SCAN", "REVISION"]


def test_dry_run_creates_nothing_and_unchanged_scans_need_no_revision(published, run):
    code, out = run("record-revisions", "--reason", "nothing logged yet")
    assert code == 0 and "Would record 0" in out
    assert not run.dir.exists()  # Not even the directory.
    run("checkpoint")
    code, out = run("record-revisions", "--reason", "nothing changed", "--apply")
    assert code == 0 and out.startswith("Recorded 0")
    lines = (run.dir / "leaves.jsonl").read_text().splitlines()
    assert [json.loads(line)["kind"] for line in lines] == ["SCAN"]


def test_modified_log_is_suspect_with_every_command(published, run):
    run("checkpoint")
    tamper(run.dir / "leaves.jsonl")
    code, report = verify_json(run)
    assert code == 1 and report["findings"] == ["LOG_INCONSISTENT"]
    assert run("append")[0] == 1
    assert run("export", str(run.dir.parent / "bundle"))[0] == 1


def test_export_and_verify_bundle_without_a_database(published, run, tmp_path):
    run("checkpoint")
    bundle = tmp_path / "bundle"
    code, out = run("export", str(bundle))
    assert code == 0 and "not implemented" in out
    assert run("export", str(bundle))[0] == 2  # Never merged into an existing directory.
    plain = {"database": False, "log": False}
    code, out = run("verify-bundle", str(bundle), "--json", **plain)
    report = json.loads(out)
    assert code == 0 and report["status"] == "ok" and "scans" not in report
    trusted = bundle / "checkpoints" / f"{1:020d}.json"
    assert run("verify-bundle", str(bundle), "--trusted-checkpoint", str(trusted), **plain)[0] == 0
    tamper(bundle / "leaves.jsonl")
    code, out = run("verify-bundle", str(bundle), "--json", **plain)
    assert code == 1 and json.loads(out)["findings"] == ["LOG_INCONSISTENT"]


def test_rolled_back_bundle_is_detected_with_a_trusted_checkpoint(tmp_path, run):
    long, short = tlog.TransparencyLog(tmp_path / "long"), tlog.TransparencyLog(tmp_path / "short")
    for log, count in ((long, 4), (short, 2)):
        with log.writing() as writer:
            for i in range(count):
                writer.add("SCAN", commitment(i))
            writer.checkpoint()
    short.export(tmp_path / "rolled-back")
    trusted = long.root / "checkpoints" / f"{4:020d}.json"
    code, out = run(
        "verify-bundle",
        str(tmp_path / "rolled-back"),
        "--trusted-checkpoint",
        str(trusted),
        "--json",
        database=False,
        log=False,
    )
    assert code == 1 and json.loads(out)["findings"] == ["CONSISTENCY_FAILED"]


def test_environment_problems_exit_with_two_and_leak_nothing(published, settings, run, tmp_path):
    secret_db = ["--database-url", "sqlite:////no/such/dir/SECRET.sqlite"]
    code, out = run("verify", *secret_db, database=False)
    assert code == 2 and "SECRET" not in out and "/no/such" not in out
    assert run("verify-bundle", str(tmp_path / "missing"), database=False, log=False)[0] == 2
    assert run("record-revisions", "--reason", "")[0] == 2
    assert run("record-revisions", "--reason", "x" * 201)[0] == 2
    bad = tmp_path / "trusted.json"
    bad.write_text("{}")
    assert run("verify", "--trusted-checkpoint", str(bad))[0] == 2
    with tlog.TransparencyLog(run.dir).writing():
        code, out = run("append")
    assert code == 2 and out.strip() == "LOG_BUSY"
    (tmp_path / "linked").symlink_to(tmp_path, target_is_directory=True)
    assert run("append", "--log-dir", str(tmp_path / "linked"), log=False)[0] == 2


def test_a_missing_scan_and_an_unlogged_scan_are_warnings_not_suspicion(published, run):
    code, report = verify_json(run)
    assert code == 0 and report["scans"]["not_logged"] and "UNLOGGED_SCANS" in report["warnings"]
    with tlog.TransparencyLog(run.dir).writing() as writer:
        writer.add("SCAN", {**commitment(0), "scan_id": str(uuid4())})
        writer.checkpoint()
    code, report = verify_json(run)
    assert code == 0 and len(report["scans"]["missing"]) == 1
    assert "MISSING_SCANS" in report["warnings"]


def test_a_result_too_large_to_commit_is_reported_and_not_logged(published, run, monkeypatch):
    from app.core.exceptions import ConflictError

    def too_large(session, scan):
        raise ConflictError("SECRET_RESULT_TEXT")

    monkeypatch.setattr(tlog, "result_export_digest", too_large)
    code, out = run("append")
    assert code == 0 and "TOO_LARGE" in out and "SECRET_RESULT_TEXT" not in out
    assert tlog.TransparencyLog(run.dir).read().entries == []


def test_anchor_failure_keeps_the_checkpoint_and_exits_two_until_it_succeeds(
    published, run, monkeypatch
):
    import httpx

    original, answer = httpx.Client, {"status": 503}

    def transport(request):
        return httpx.Response(answer["status"], content=b"receipt")

    monkeypatch.setattr(
        httpx, "Client", lambda **kw: original(transport=httpx.MockTransport(transport), **kw)
    )
    monkeypatch.setenv("MODULEGUARD_TRANSPARENCY_ANCHOR", "opentimestamps")
    monkeypatch.setenv("MODULEGUARD_OTS_CALENDARS", "https://calendar.test")
    code, out = run("checkpoint")
    assert code == 2 and "failed ANCHOR_UNAVAILABLE" in out
    assert (run.dir / "checkpoints" / f"{1:020d}.json").exists()
    answer["status"] = 200
    code, out = run("checkpoint")
    assert code == 0 and "checkpoint unchanged" in out and "submitted" in out
    code, report = verify_json(run)
    assert code == 0 and report["anchors"] == [
        {"tree_size": 1, "type": "opentimestamps", "status": "submitted"}
    ]
    assert "OTS_NOT_VERIFIED" in report["warnings"]
    monkeypatch.setenv("MODULEGUARD_TRANSPARENCY_ANCHOR", "ethereum")
    assert run("checkpoint")[0] == 2


# ------------------------------------------------------------------------- HTTP API


def transparency_path(scan_id, version_id):
    return f"/api/scans/{scan_id}/transparency?version_id={version_id}"


@pytest.fixture
def api(published):
    client, scan_id, version_id = published
    log = tlog.TransparencyLog(client.scans.snapshot_store.root / "transparency")
    return client, scan_id, version_id, log, transparency_path(scan_id, version_id)


def log_scans(sessions, log, *, checkpoint=True):
    with sessions() as session, log.writing() as writer:
        tlog.append_scans(session, writer)
        if checkpoint:
            writer.checkpoint()


def test_checkpoint_without_a_log_is_disabled(api):
    client, *_ = api
    response = client.get("/api/transparency/checkpoint")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert response.json() == {
        "schema_version": 1,
        "enabled": False,
        "checkpoint": None,
        "anchors": [],
    }


def test_checkpoint_shows_only_public_tree_head_data(api, sessions):
    client, _, _, log, _ = api
    log_scans(sessions, log)
    head = log.read().tree_head
    with log.writing() as writer:
        writer.put_anchor(
            {
                "type": "opentimestamps",
                "status": "submitted",
                "tree_size": 1,
                "root_hash": head["root_hash"],
                "receipts_b64": ["c2VjcmV0LXJlY2VpcHQ="],
                "receipt_sha256": tlog.receipt_sha256(["c2VjcmV0LXJlY2VpcHQ="]),
                "created_at": "2026-11-26T05:00:03+00:00",
            }
        )
    response = client.get("/api/transparency/checkpoint")
    body = response.json()
    assert response.status_code == 200 and body["enabled"] is True
    assert body["checkpoint"] == {
        "tree_size": 1,
        "root_hash": head["root_hash"],
        "created_at": head["created_at"].replace("+00:00", "Z"),
    }
    [anchor] = body["anchors"]
    assert anchor["type"] == "opentimestamps" and anchor["status"] == "submitted"
    assert anchor["receipt_sha256"] == tlog.receipt_sha256(["c2VjcmV0LXJlY2VpcHQ="])
    assert "c2VjcmV0LXJlY2VpcHQ" not in response.text  # The receipt itself is not served.


def test_consistency_proof_verifies_with_the_published_roots(api, sessions):
    client, _, _, log, _ = api
    log_scans(sessions, log)
    with log.writing() as writer:
        for i in range(4):
            writer.add("SCAN", commitment(i))
        writer.checkpoint()
    response = client.get("/api/transparency/consistency?first=1&second=5")
    body = response.json()
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert body["schema_version"] == 1 and (body["first"], body["second"]) == (1, 5)
    assert merkle.verify_consistency(
        1,
        5,
        bytes.fromhex(body["first_root"]),
        bytes.fromhex(body["second_root"]),
        [bytes.fromhex(p) for p in body["proof"]],
    )
    assert client.get("/api/transparency/consistency?first=5&second=5").json()["proof"] == []
    for query in ("first=2&second=5", "first=1&second=9", "first=5&second=1"):
        assert client.get(f"/api/transparency/consistency?{query}").status_code == 404
    for query in ("first=0&second=5", "first=x&second=5", "second=5"):
        assert client.get(f"/api/transparency/consistency?{query}").status_code == 422


def test_scan_status_moves_from_not_logged_to_pending_to_included(api, sessions):
    client, scan_id, version_id, log, path = api
    response = client.get(path)
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["status"] == "not_logged" and body["reasons"] == []
    assert body["entry"] is None and body["leaf_index"] is None and body["checkpoint"] is None
    assert body["current_commitment_sha256"]

    log_scans(sessions, log, checkpoint=False)
    body = client.get(path).json()
    assert body["status"] == "pending" and body["reasons"] == ["NOT_IN_CHECKPOINT"]
    assert body["leaf_index"] == 0 and body["checkpoint"] is None and body["inclusion_proof"] == []

    with log.writing() as writer:
        writer.checkpoint()
    body = client.get(path).json()
    assert body["status"] == "included" and body["reasons"] == []
    assert body["scan_id"] == str(scan_id) and body["version_id"] == str(version_id)
    assert body["entry"]["kind"] == "SCAN" and body["entry"]["seq"] == 0
    assert body["entry"]["commitment"]["scan_id"] == str(scan_id)
    assert body["entry"]["commitment"]["module_version_id"] == str(version_id)
    assert body["current_commitment_sha256"] == body["logged_commitment_sha256"]
    assert body["revisions"] == 0
    # An independent verifier needs nothing but this response and the checkpoint root.
    from app.attestations.signing import canonical

    leaf = merkle.leaf_hash(canonical(body["entry"]))
    assert leaf.hex() == body["leaf_hash"]
    assert merkle.verify_inclusion(
        leaf,
        body["leaf_index"],
        body["checkpoint"]["tree_size"],
        [bytes.fromhex(p) for p in body["inclusion_proof"]],
        bytes.fromhex(body["checkpoint"]["root_hash"]),
    )
    assert (
        body["checkpoint"]["root_hash"]
        == client.get("/api/transparency/checkpoint").json()["checkpoint"]["root_hash"]
    )


def test_changed_result_is_a_mismatch_and_a_declared_revision_clears_it(api, sessions):
    client, scan_id, _, log, path = api
    log_scans(sessions, log)
    with sessions() as session:
        finding = session.scalar(select(Finding).where(Finding.scan_id == scan_id))
        finding.raw_data = {**finding.raw_data, "private": "SECRET_RAW_FINDING"}
        finding.guideline = "SECRET_GUIDELINE_TEXT"
        session.commit()
    response = client.get(path)
    body = response.json()
    assert body["status"] == "mismatch" and body["reasons"] == ["COMMITMENT_MISMATCH"]
    assert body["current_commitment_sha256"] != body["logged_commitment_sha256"]
    assert body["inclusion_proof"] == [] and body["checkpoint"]["tree_size"] == 1
    for secret in ("SECRET_RAW_FINDING", "SECRET_GUIDELINE_TEXT", "guideline", "findings"):
        assert secret not in response.text
    assert (
        main(
            [
                "record-revisions",
                "--reason",
                "reapply",
                "--apply",
                "--log-dir",
                str(log.root),
                "--database-url",
                client.app.state.settings.database_url,
            ]
        )
        == 0
    )
    with log.writing() as writer:
        writer.checkpoint()
    body = client.get(path).json()
    assert body["status"] == "included" and body["revisions"] == 1
    assert body["entry"]["kind"] == "REVISION" and body["entry"]["reason"] == "reapply"


def test_result_too_large_and_unfinished_scans_report_fixed_reasons(api, sessions, monkeypatch):
    from app.core.exceptions import ConflictError

    client, scan_id, _, log, path = api

    def too_large(session, scan):
        raise ConflictError("SECRET_RESULT_TEXT")

    monkeypatch.setattr(tlog, "result_export_digest", too_large)
    response = client.get(path)
    assert response.json()["status"] == "not_logged" and response.json()["reasons"] == ["TOO_LARGE"]
    assert "SECRET_RESULT_TEXT" not in response.text
    monkeypatch.undo()
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        scan.status, scan.finished_at = ScanStatus.SCANNING, None
        session.commit()
    assert client.get(path).json()["reasons"] == ["SCAN_NOT_FINISHED"]


def test_visibility_is_the_same_as_attestations(api, sessions):
    client, scan_id, version_id, log, path = api
    log_scans(sessions, log)
    other = f"/api/scans/{scan_id}/transparency?version_id={uuid4()}"
    assert client.get(other).status_code == 404  # Another version of nothing.
    assert client.get(transparency_path(uuid4(), version_id)).status_code == 404
    assert client.get(f"/api/scans/{scan_id}/transparency").status_code == 422
    assert client.patch("/api/repositories/demo", json={"visibility": "PRIVATE"}).status_code == 204
    assert client.get(path).status_code == 200  # The owner still sees it.
    client.cookies.clear()
    hidden = client.get(path)
    assert hidden.status_code == 404
    assert hidden.json() == client.get(transparency_path(uuid4(), version_id)).json()
    assert client.get("/api/transparency/checkpoint").status_code == 200  # Public tree head.


def test_a_forks_view_never_names_a_hidden_original(api, sessions):
    client, scan_id, version_id, log, _ = api
    sign_in(client, sessions, "bob", 2)
    forked = client.post("/api/repositories/alice/demo/fork", json={"visibility": "PUBLIC"})
    assert forked.status_code == 201, forked.text
    with sessions() as session:
        versions = list(
            session.scalars(select(ModuleVersion).where(ModuleVersion.scan_id == scan_id))
        )
        fork_id = next(v.id for v in versions if v.id != version_id)
    log_scans(sessions, log)
    path = transparency_path(scan_id, fork_id)
    shown = client.get(path).json()  # The original is still public: bob may see its id.
    assert shown["entry"]["commitment"]["module_version_id"] == str(version_id)
    sign_in(client, sessions)
    assert client.patch("/api/repositories/demo", json={"visibility": "PRIVATE"}).status_code == 204
    client.cookies.clear()
    response = client.get(path)
    body = response.json()
    assert response.status_code == 200 and body["version_id"] == str(fork_id)
    assert body["status"] == "included" and body["entry"] is None
    assert body["leaf_hash"] and body["checkpoint"]  # The proof is still served.
    assert str(version_id) not in response.text
    assert client.get(transparency_path(scan_id, version_id)).status_code == 404


def test_a_broken_log_is_reported_without_affecting_other_endpoints(api, sessions):
    client, scan_id, version_id, log, path = api
    log_scans(sessions, log)
    tamper(log.root / "leaves.jsonl")
    body = client.get(path).json()
    assert body["status"] == "mismatch" and "LOG_INCONSISTENT" in body["reasons"]
    response = client.get("/api/transparency/checkpoint")
    assert response.status_code == 503 and response.headers["cache-control"] == "no-store"
    assert response.json()["error"]["code"] == "LOG_INCONSISTENT"
    assert "COMPLETED" not in response.text
    assert client.get("/api/transparency/consistency?first=1&second=1").status_code == 503
    # Scans, graphs and attestations do not depend on the log at all.
    assert client.get(f"/api/scans/{scan_id}").status_code == 200
    assert client.get(f"/api/scans/{scan_id}/graph").status_code == 200
    attest = client.get(f"/api/scans/{scan_id}/attestations?version_id={version_id}")
    assert attest.status_code == 200 and attest.json()["status"] == "unsigned"


def test_an_unreadable_log_directory_is_unavailable_not_inconsistent(api, tmp_path):
    client, scan_id, version_id, log, path = api
    log.root.parent.mkdir(parents=True, exist_ok=True)
    log.root.symlink_to(tmp_path, target_is_directory=True)
    body = client.get(path).json()
    assert body["status"] == "not_logged" and body["reasons"] == ["LOG_UNAVAILABLE"]
    response = client.get("/api/transparency/checkpoint")
    assert response.status_code == 503 and response.json()["error"]["code"] == "LOG_UNAVAILABLE"
    assert str(tmp_path) not in response.text


def test_transparency_reads_are_rate_limited(api):
    client, *_ = api
    client.app.state.usage_service.preview_limiter.per_hour = 2
    codes = [client.get("/api/transparency/checkpoint").status_code for _ in range(4)]
    assert codes[:2] == [200, 200] and 429 in codes
