import os
import stat
from datetime import UTC, datetime
from uuid import UUID

import pytest
from conftest import publish, sign_in
from sqlalchemy import select

from app.attestations.signing import canonical
from app.models import Finding, ModuleVersion, Scan
from app.transparency import log as tlog
from app.transparency import merkle
from app.transparency.log import (
    LogBusy,
    LogInconsistent,
    LogUnavailable,
    TransparencyLog,
    append_scans,
    changed_scans,
    check_log,
    describe_scan,
    record_revisions,
)


def commitment(i=0, version=None):
    return {
        "scan_id": str(UUID(int=i + 1)),
        "module_version_id": version,
        "status": "COMPLETED",
        "result_sha256": f"{i:064x}",
        "manifest_sha256": None,
        "snapshot_sha256": None,
    }


@pytest.fixture
def log(tmp_path):
    return TransparencyLog(tmp_path / "transparency")


def fill(log, count, checkpoints=()):
    """Entries for `count` scans, with a checkpoint after each size in `checkpoints`."""
    with log.writing() as writer:
        for i in range(count):
            writer.add("SCAN", commitment(i))
            if i + 1 in checkpoints:
                writer.checkpoint()
        writer.sync()
    return log


def leaves(log):
    return log.root / "leaves.jsonl"


def rewrite(log, edit):
    lines = leaves(log).read_bytes().split(b"\n")[:-1]
    leaves(log).write_bytes(b"".join(line + b"\n" for line in edit(lines)))


def test_append_checkpoint_roundtrip_with_private_modes(log):
    fill(log, 3, checkpoints=(3,))
    state = log.read(full=True)
    assert [e["seq"] for e in state.entries] == [0, 1, 2]
    [head] = state.checkpoints
    assert head["tree_size"] == 3 and head["previous"] is None
    assert head["root_hash"] == merkle.root(state.hashes).hex()
    assert stat.S_IMODE(log.root.stat().st_mode) == 0o700
    assert stat.S_IMODE(leaves(log).stat().st_mode) == 0o600
    assert stat.S_IMODE((log.root / "checkpoints").stat().st_mode) == 0o700
    for file in (log.root / "checkpoints").iterdir():
        assert stat.S_IMODE(file.stat().st_mode) == 0o600
    assert check_log(state) == []


def test_missing_log_is_an_empty_log_and_checkpoint_is_idempotent(log):
    assert log.read().entries == [] and not log.read().exists
    with log.writing() as writer:
        assert writer.checkpoint() is None  # Nothing to attest yet.
        writer.add("SCAN", commitment(0))
        first = writer.checkpoint()
        assert writer.checkpoint() is None
        writer.add("SCAN", commitment(1))
        second = writer.checkpoint()
    assert second["previous"] == {"tree_size": 1, "root_hash": first["root_hash"]}
    assert [c["tree_size"] for c in log.read(full=True).checkpoints] == [1, 2]


def test_a_second_writer_fails_at_once_and_the_log_stays_usable(log):
    fill(log, 1)
    with log.writing():
        with pytest.raises(LogBusy):
            with log.writing():
                pass
    assert len(log.read().entries) == 1
    with log.writing() as writer:
        writer.add("SCAN", commitment(1))
    assert len(log.read().entries) == 2


@pytest.mark.parametrize(
    "edit",
    [
        lambda lines: [lines[0].replace(b"0" * 10, b"1" * 10), *lines[1:]],  # entry modified
        lambda lines: lines[:1] + lines[2:],  # entry deleted
        lambda lines: [*lines[:1], lines[0], *lines[1:]],  # entry duplicated / inserted
        lambda lines: [lines[1], lines[0], *lines[2:]],  # reordered
        lambda lines: lines[:-1],  # truncated below the last checkpoint
        lambda lines: [lines[0].replace(b'":"', b'": "', 1), *lines[1:]],  # not canonical
        lambda lines: [b"x" * 5000, *lines[1:]],  # oversized line
        lambda lines: [b'{"seq":0,"seq":0}', *lines[1:]],  # duplicate keys
    ],
)
def test_edited_deleted_inserted_or_reordered_entries_are_detected(log, edit):
    fill(log, 4, checkpoints=(4,))
    rewrite(log, edit)
    with pytest.raises(LogInconsistent):
        log.read()


def test_torn_final_line_is_inconsistent_not_guessed(log):
    fill(log, 2, checkpoints=(2,))
    with leaves(log).open("ab") as stream:
        stream.write(b'{"format":1')
    with pytest.raises(LogInconsistent):
        log.read()


def test_a_rewritten_older_checkpoint_fails_consistency_not_just_structure(log):
    fill(log, 4, checkpoints=(2, 4))
    first = log.root / "checkpoints" / f"{2:020d}.json"
    second = log.root / "checkpoints" / f"{4:020d}.json"
    forged = merkle.root([merkle.leaf_hash(b"other")]).hex()
    from app.attestations.signing import strict_json

    old = strict_json(first.read_bytes())
    new = strict_json(second.read_bytes())
    old["root_hash"], new["previous"]["root_hash"] = forged, forged
    first.write_bytes(canonical(old))
    second.write_bytes(canonical(new))
    state = log.read()  # The newest head is still reproducible from the entries.
    assert check_log(state) == ["CONSISTENCY_FAILED"]
    with pytest.raises(LogInconsistent):
        log.read(full=True)


def test_deleting_or_replacing_a_middle_checkpoint_is_detected(log):
    fill(log, 6, checkpoints=(2, 4, 6))
    (log.root / "checkpoints" / f"{4:020d}.json").unlink()
    with pytest.raises(LogInconsistent):
        log.read()


def test_links_and_special_files_are_refused(log, tmp_path):
    fill(log, 2, checkpoints=(2,))
    outside = tmp_path / "private"
    outside.write_text("SECRET")
    target = log.root / "checkpoints" / f"{2:020d}.json"
    saved = target.read_bytes()
    target.unlink()
    target.symlink_to(outside)
    with pytest.raises(LogUnavailable) as error:
        log.read()
    assert "SECRET" not in str(error.value)
    target.unlink()
    target.write_bytes(saved)
    leaves(log).rename(tmp_path / "moved")
    leaves(log).symlink_to(tmp_path / "moved")
    with pytest.raises(LogUnavailable):
        log.read()
    leaves(log).unlink()
    os.mkfifo(leaves(log))
    with pytest.raises(LogUnavailable):
        log.read()
    link = TransparencyLog(tmp_path / "linked")
    link.root.symlink_to(log.root, target_is_directory=True)
    with pytest.raises(LogUnavailable):
        link.read()
    with pytest.raises(LogUnavailable):
        with link.writing():
            pass


def test_a_file_that_changed_after_it_was_read_is_not_appended_to(log):
    fill(log, 1)
    with log.writing() as writer:
        with leaves(log).open("ab") as stream:
            stream.write(b"extra")
        with pytest.raises(LogInconsistent):
            writer.add("SCAN", commitment(1))


def test_a_scan_may_only_be_revised_after_it_was_logged(log):
    fill(log, 1)
    with log.writing() as writer:
        writer.add("SCAN", commitment(0))  # A second first entry for the same scan.
    with pytest.raises(LogInconsistent):
        log.read()
    other = TransparencyLog(log.root.parent / "other")
    with other.writing() as writer:
        writer.add("REVISION", commitment(0), "no earlier entry")
    with pytest.raises(LogInconsistent):
        other.read()


def test_reason_is_required_and_bounded(log):
    with log.writing() as writer:
        writer.add("SCAN", commitment(0))
        for reason in ("", "x" * 201):
            with pytest.raises(LogUnavailable):
                writer.add("REVISION", commitment(0), reason)
        writer.add("REVISION", commitment(0), "가" * 200)  # Under the 4 KiB line limit.
    assert log.read().revisions == {str(UUID(int=1)): 1}


def anchor(size, root, kind="opentimestamps", status="submitted", receipts=("YQ==",)):
    return {
        "type": kind,
        "status": status,
        "tree_size": size,
        "root_hash": root,
        "receipts_b64": list(receipts),
        "receipt_sha256": tlog.receipt_sha256(list(receipts)) if receipts else None,
        "created_at": datetime.now(UTC).isoformat(),
    }


def test_anchor_states_failed_may_be_retried_and_submitted_is_final(log):
    fill(log, 2, checkpoints=(2,))
    root = log.read().tree_head["root_hash"]
    failed = {**anchor(2, root, status="failed", receipts=()), "code": "ANCHOR_UNAVAILABLE"}
    del failed["receipt_sha256"]
    with log.writing() as writer:
        assert writer.put_anchor(failed)
        assert writer.put_anchor(anchor(2, root))  # The retry replaces the failed record.
        assert not writer.put_anchor(failed)  # A submitted anchor is never overwritten.
    state = log.read()
    assert state.anchors[(2, "opentimestamps")]["status"] == "submitted"
    assert check_log(state) == []


def test_anchor_for_another_root_or_with_a_changed_receipt_is_a_mismatch(log):
    fill(log, 2, checkpoints=(2,))
    root = log.read().tree_head["root_hash"]
    with log.writing() as writer:
        writer.put_anchor({**anchor(2, "ab" * 32)})
    assert check_log(log.read()) == ["ANCHOR_MISMATCH"]
    other = TransparencyLog(log.root.parent / "other")
    fill(other, 2, checkpoints=(2,))
    record = {**anchor(2, root), "receipts_b64": ["Yg=="]}  # Receipt differs from its hash.
    with other.writing() as writer:
        writer.put_anchor(record)
    assert check_log(other.read()) == ["ANCHOR_MISMATCH"]


def test_anchor_without_a_checkpoint_is_inconsistent(log):
    fill(log, 2)
    with log.writing() as writer:
        writer.put_anchor(anchor(2, "ab" * 32))
    with pytest.raises(LogInconsistent):
        log.read()


def test_trusted_checkpoint_detects_rollback_and_a_rewritten_history(log, tmp_path):
    fill(log, 4, checkpoints=(2, 4))
    state = log.read()
    trusted = state.tree_head
    assert check_log(state, trusted) == []
    assert check_log(state, state.checkpoints[0]) == []  # An older head is a prefix.
    shorter = TransparencyLog(tmp_path / "shorter")
    fill(shorter, 2, checkpoints=(2,))
    assert check_log(shorter.read(), trusted) == ["CONSISTENCY_FAILED"]  # Rolled back.
    forged = TransparencyLog(tmp_path / "forged")
    with forged.writing() as writer:
        for i in range(4):
            writer.add("SCAN", commitment(i + 100))
        writer.checkpoint()
    assert check_log(forged.read(), trusted) == ["CONSISTENCY_FAILED"]  # Same size, new history.
    assert check_log(TransparencyLog(tmp_path / "nothing").read(), trusted) == [
        "CONSISTENCY_FAILED"
    ]


def test_trusted_checkpoint_file_is_validated(log, tmp_path):
    fill(log, 2, checkpoints=(2,))
    good = log.root / "checkpoints" / f"{2:020d}.json"
    assert tlog.load_trusted(good)["tree_size"] == 2
    bad = tmp_path / "bad.json"
    bad.write_text('{"tree_size":2}')
    with pytest.raises(LogInconsistent):
        tlog.load_trusted(bad)
    with pytest.raises(LogUnavailable):
        tlog.load_trusted(tmp_path / "missing.json")


def test_export_is_a_verifiable_copy_and_refuses_a_used_directory(log, tmp_path):
    fill(log, 3, checkpoints=(2, 3))
    with log.writing() as writer:
        writer.put_anchor(anchor(3, log.read().tree_head["root_hash"]))
    bundle = tmp_path / "bundle"
    log.export(bundle)
    copy = TransparencyLog(bundle).read(full=True)
    original = log.read(full=True)
    assert copy.entries == original.entries and copy.checkpoints == original.checkpoints
    assert copy.anchors == original.anchors and check_log(copy) == []
    with pytest.raises(LogUnavailable):
        log.export(bundle)


# ----------------------------------------------------------------- against the database


@pytest.fixture
def scan_stack(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client)
    return client, UUID(created["scan_id"])


def test_commitment_is_stable_and_names_the_stored_hashes(scan_stack, sessions):
    _, scan_id = scan_stack
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        first = tlog.current_commitment(session, scan)
        assert first == tlog.current_commitment(session, scan)
        version = session.scalar(select(ModuleVersion).where(ModuleVersion.scan_id == scan_id))
        assert first["scan_id"] == str(scan_id)
        assert first["module_version_id"] == str(version.id)
        assert first["manifest_sha256"] == version.manifest_sha256
        assert first["snapshot_sha256"] == version.snapshot_sha256
        assert first["status"] == "COMPLETED"


def test_append_is_idempotent_and_a_changed_finding_is_a_mismatch(scan_stack, sessions, log):
    _, scan_id = scan_stack
    with sessions() as session, log.writing() as writer:
        assert tlog.append_scans(session, writer) == (1, {})
        assert tlog.append_scans(session, writer) == (0, {})
        writer.checkpoint()
    state = log.read()
    assert state.entries[0]["kind"] == "SCAN" and state.entries[0]["reason"] is None
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        assert describe_scan(session, scan, state)["status"] == "included"
        finding = session.scalar(select(Finding).where(Finding.scan_id == scan_id))
        finding.guideline = "edited after it was logged"
        session.commit()
        described = describe_scan(session, session.get(Scan, scan_id), state)
        assert described["status"] == "mismatch"
        assert described["reasons"] == ["COMMITMENT_MISMATCH"]
        assert described["logged_commitment_sha256"] != described["current_commitment_sha256"]
        # The originally logged value stays provable against the checkpoint.
        assert merkle.verify_inclusion(
            bytes.fromhex(described["leaf_hash"]),
            described["leaf_index"],
            described["checkpoint"]["tree_size"],
            [bytes.fromhex(p) for p in described["inclusion_proof"]],
            bytes.fromhex(described["checkpoint"]["root_hash"]),
        )


def test_dry_run_writes_nothing_and_apply_adds_a_revision_that_stays(scan_stack, sessions, log):
    _, scan_id = scan_stack
    with sessions() as session, log.writing() as writer:
        append_scans(session, writer)
        writer.checkpoint()
    with sessions() as session:
        finding = session.scalar(select(Finding).where(Finding.scan_id == scan_id))
        finding.guideline = "legitimately changed"
        session.commit()
    before = leaves(log).read_bytes()
    with sessions() as session:
        changed, _ = changed_scans(session, log.read())
    assert [scan for scan, _ in changed] == [str(scan_id)]
    assert leaves(log).read_bytes() == before  # A dry run only reads.
    with sessions() as session, log.writing() as writer:
        assert record_revisions(writer, "reapply 2026-11-26", changed) == 1
        writer.checkpoint()
    state = log.read(full=True)
    assert [e["kind"] for e in state.entries] == ["SCAN", "REVISION"]
    assert state.entries[1]["reason"] == "reapply 2026-11-26"
    with sessions() as session:
        assert changed_scans(session, state)[0] == []
        described = describe_scan(session, session.get(Scan, scan_id), state)
        assert described["status"] == "included" and described["revisions"] == 1
        assert described["leaf_index"] == 1
    # The first entry is still in the log, and still provable against the checkpoint.
    head = state.tree_head
    assert merkle.verify_inclusion(
        state.hashes[0],
        0,
        head["tree_size"],
        merkle.inclusion_proof(state.hashes, 0, head["tree_size"]),
        bytes.fromhex(head["root_hash"]),
    )


def test_status_of_a_scan_not_in_the_log_or_not_yet_in_a_checkpoint(scan_stack, sessions, log):
    _, scan_id = scan_stack
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        assert describe_scan(session, scan, log.read())["status"] == "not_logged"
    with sessions() as session, log.writing() as writer:
        append_scans(session, writer)
    with sessions() as session:
        described = describe_scan(session, session.get(Scan, scan_id), log.read())
        assert described["status"] == "pending"
        assert described["reasons"] == ["NOT_IN_CHECKPOINT"]
        assert described["checkpoint"] is None and described["inclusion_proof"] == []


def test_reconcile_reports_every_state(scan_stack, sessions, log):
    _, scan_id = scan_stack
    with sessions() as session:
        report = tlog.reconcile(session, log.read())
        assert report["not_logged"] == [str(scan_id)]
    with sessions() as session, log.writing() as writer:
        append_scans(session, writer)
    with sessions() as session:
        assert tlog.reconcile(session, log.read())["pending"] == 1
    with log.writing() as writer:
        writer.checkpoint()
        writer.add("SCAN", commitment(41))  # Logged, but the database has no such scan.
    with sessions() as session:
        report = tlog.reconcile(session, log.read())
        assert report["ok"] == 1 and report["missing"] == [str(UUID(int=42))]
        finding = session.scalar(select(Finding).where(Finding.scan_id == scan_id))
        finding.guideline = "tampered"
        session.commit()
        assert tlog.reconcile(session, log.read())["mismatch"] == [str(scan_id)]


def test_unfinished_scans_are_not_logged(scan_stack, sessions, log):
    from app.models.enums import ScanStatus

    _, scan_id = scan_stack
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        scan.status, scan.finished_at = ScanStatus.SCANNING, None
        session.commit()
    with sessions() as session, log.writing() as writer:
        assert append_scans(session, writer) == (0, {})
    assert log.read().entries == []
