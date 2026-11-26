import json
from uuid import UUID, uuid4

import pytest
from conftest import publish, sign_in
from sqlalchemy import select

from app.models import Finding, ModuleVersion
from app.services.attestation_service import AttestationService, result_export_digest
from app.transparency import log as tlog
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
