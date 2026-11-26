"""Operate and check the scan transparency log.

    python -m app.transparency.cli append
    python -m app.transparency.cli checkpoint                # append, new tree head, anchor
    python -m app.transparency.cli record-revisions --reason "reapply 2026-11-26" [--apply]
    python -m app.transparency.cli verify [--json] [--trusted-checkpoint FILE]
    python -m app.transparency.cli export DIR
    python -m app.transparency.cli verify-bundle DIR [--trusted-checkpoint FILE]

Exit codes: 0 fine, 1 tampering suspected (MISMATCH, LOG_INCONSISTENT, CONSISTENCY_FAILED,
ANCHOR_MISMATCH), 2 environment problem (database, storage, lock, anchor, bad arguments).
Output never contains stored result text, external error text or URLs.

See docs/scan-transparency-log.md.
"""

import argparse
import json
import sys
from pathlib import Path
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

from app.core.exceptions import DomainError
from app.transparency import anchors, merkle
from app.transparency import log as tlog
from app.transparency.log import (
    LogInconsistent,
    LogUnavailable,
    TransparencyLog,
    check_log,
)

OK, SUSPECT, ENVIRONMENT = 0, 1, 2


def _sessions(args):
    from app.core.config import Settings
    from app.core.database import create_session_factory

    return create_session_factory(args.database_url or Settings().database_url)


def _log(args) -> TransparencyLog:
    from app.core.config import Settings

    return TransparencyLog(
        Path(args.log_dir) if args.log_dir else Settings().snapshot_root / "transparency"
    )


def _trusted(args):
    if not args.trusted_checkpoint:
        return None
    try:
        return tlog.load_trusted(Path(args.trusted_checkpoint))
    except LogInconsistent:
        raise LogUnavailable("신뢰 체크포인트 파일이 올바르지 않습니다.") from None


def _head(state) -> str:
    head = state.tree_head
    return (
        "none" if head is None else f"tree_size={head['tree_size']} root_hash={head['root_hash']}"
    )


def append(args) -> int:
    with _log(args).writing() as writer, _sessions(args)() as session:
        appended, skipped = tlog.append_scans(session, writer)
    print(f"appended={appended} not_logged={len(skipped)}")
    for scan_id, code in skipped.items():
        print(f"not_logged {scan_id} {code}")
    return OK


def checkpoint(args) -> int:
    kind = anchors.configured_type()
    with _log(args).writing() as writer, _sessions(args)() as session:
        appended, skipped = tlog.append_scans(session, writer)
        created = writer.checkpoint()
        print(f"appended={appended} not_logged={len(skipped)}")
        print(f"checkpoint {'created' if created else 'unchanged'}: {_head(writer.state)}")
        # The checkpoint is already stored; an anchor problem never undoes it.
        results = anchors.anchor_pending(writer, kind)
    for result in results:
        print(
            f"anchor {result['type']} tree_size={result['tree_size']} {result['status']}"
            + (f" {result['code']}" if result["code"] else "")
        )
    return ENVIRONMENT if any(r["status"] == "failed" for r in results) else OK


def record_revisions(args) -> int:
    reason = args.reason.strip()
    if not 0 < len(reason) <= tlog.MAX_REASON:
        raise LogUnavailable("사유는 1자 이상 200자 이하여야 합니다.")
    log = _log(args)
    with _sessions(args)() as session:
        if not args.apply:
            changed, skipped = tlog.changed_scans(session, log.read(), args.scan_id)
        else:
            with log.writing() as writer:
                changed, skipped = tlog.changed_scans(session, writer.state, args.scan_id)
                tlog.record_revisions(writer, reason, changed)
    for scan_id, _ in changed:
        print(f"{'revision' if args.apply else 'would record revision'} {scan_id}")
    for scan_id, code in skipped.items():
        print(f"skipped {scan_id} {code}")
    if args.apply:
        print(f"Recorded {len(changed)} revision(s). Run `checkpoint` to include them.")
    else:
        print(
            f"Would record {len(changed)} revision(s). Dry run: nothing written. Re-run with --apply."
        )
    return OK


def _report(state, trusted, scans=None) -> dict:
    findings = check_log(state, trusted)
    anchor_findings, warnings = anchors.verify_anchors(state, _policy())
    findings += anchor_findings
    if scans is not None and scans["mismatch"]:
        findings.append("MISMATCH")
    head = state.tree_head
    report = {
        "status": "suspect" if findings else "ok",
        "exit_code": SUSPECT if findings else OK,
        "findings": findings,
        "warnings": warnings
        + (["MISSING_SCANS"] if scans and scans["missing"] else [])
        + (["UNLOGGED_SCANS"] if scans and scans["not_logged"] else []),
        "log": {
            "entries": len(state.entries),
            "checkpoints": len(state.checkpoints),
            "tree_size": head["tree_size"] if head else 0,
            "root_hash": head["root_hash"] if head else merkle.EMPTY_ROOT.hex(),
        },
        "anchors": [
            {"tree_size": size, "type": kind, "status": record["status"]}
            for (size, kind), record in sorted(state.anchors.items())
        ],
    }
    if scans is not None:
        report["scans"] = scans
    return report


def _policy():
    from app.services.attestation_service import load_policy

    return load_policy()


def _print(report, as_json) -> int:
    if as_json:
        print(json.dumps(report, ensure_ascii=False, indent=2))
        return report["exit_code"]
    log = report["log"]
    print(f"status: {report['status']}")
    print(
        f"log: entries={log['entries']} checkpoints={log['checkpoints']} "
        f"tree_size={log['tree_size']} root_hash={log['root_hash']}"
    )
    for anchor in report["anchors"]:
        print(f"anchor: {anchor['type']} tree_size={anchor['tree_size']} {anchor['status']}")
    if "scans" in report:
        scans = report["scans"]
        print(
            "scans: "
            + " ".join(
                f"{name}={scans[name] if name in ('ok', 'pending') else len(scans[name])}"
                for name in ("ok", "pending", "mismatch", "missing", "not_logged")
            )
        )
        for name in ("mismatch", "missing", "not_logged"):
            for scan_id in scans[name]:
                print(f"  {name}: {scan_id}")
    for code in report["findings"]:
        print(f"finding: {code}")
    for code in report["warnings"]:
        print(f"warning: {code}")
    return report["exit_code"]


def _inconsistent(as_json) -> int:
    report = {
        "status": "suspect",
        "exit_code": SUSPECT,
        "findings": ["LOG_INCONSISTENT"],
        "warnings": [],
        "log": {
            "entries": 0,
            "checkpoints": 0,
            "tree_size": 0,
            "root_hash": merkle.EMPTY_ROOT.hex(),
        },
        "anchors": [],
    }
    return _print(report, as_json)


def verify(args) -> int:
    trusted = _trusted(args)
    try:
        state = _log(args).read()
    except LogInconsistent:
        return _inconsistent(args.json)
    with _sessions(args)() as session:
        scans = tlog.reconcile(session, state)
    return _print(_report(state, trusted, scans), args.json)


def export(args) -> int:
    state = _log(args).export(Path(args.directory))
    print(
        f"exported entries={len(state.entries)} checkpoints={len(state.checkpoints)} "
        f"anchors={len(state.anchors)} to {args.directory}"
    )
    print("note: .ots receipts are not produced (not implemented / not verified)")
    return OK


def verify_bundle(args) -> int:
    trusted = _trusted(args)
    try:
        state = TransparencyLog(Path(args.directory)).read(full=True)
    except LogInconsistent:
        return _inconsistent(args.json)
    if not state.exists:
        raise LogUnavailable("내보낸 묶음을 찾을 수 없습니다.")
    return _print(_report(state, trusted), args.json)


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(
        prog="python -m app.transparency.cli",
        description="Operate and verify the ModuleGuard scan transparency log.",
    )
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--database-url", help="defaults to DATABASE_URL / .env")
    common.add_argument("--log-dir", help="defaults to <SNAPSHOT_ROOT>/transparency")
    trusted = argparse.ArgumentParser(add_help=False)
    trusted.add_argument("--trusted-checkpoint", metavar="FILE")
    trusted.add_argument("--json", action="store_true")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("append", parents=[common], help="log finished scans not yet recorded")
    commands.add_parser("checkpoint", parents=[common], help="append, checkpoint and anchor")
    revisions = commands.add_parser(
        "record-revisions", parents=[common], help="declare legitimate result changes"
    )
    revisions.add_argument("--reason", required=True)
    revisions.add_argument("--scan-id", nargs="+", type=UUID, metavar="ID")
    revisions.add_argument("--apply", action="store_true", help="write (default: dry run)")
    commands.add_parser("verify", parents=[common, trusted], help="check the log and the database")
    exporting = commands.add_parser("export", parents=[common], help="write a bundle")
    exporting.add_argument("directory")
    bundle = commands.add_parser(
        "verify-bundle", parents=[trusted], help="check an exported bundle without a database"
    )
    bundle.add_argument("directory")
    return root


COMMANDS = {
    "append": append,
    "checkpoint": checkpoint,
    "record-revisions": record_revisions,
    "verify": verify,
    "export": export,
    "verify-bundle": verify_bundle,
}


def main(argv: list[str] | None = None) -> int:
    args = parser().parse_args(argv)
    try:
        return COMMANDS[args.command](args)
    except LogInconsistent:
        print("LOG_INCONSISTENT")
        return SUSPECT
    except anchors.AnchorConfigError as error:
        print(f"ANCHOR_CONFIG {error}")
        return ENVIRONMENT
    except DomainError as error:
        print(getattr(error, "code", "UNAVAILABLE"))
        return ENVIRONMENT
    except SQLAlchemyError:
        print("DATABASE_UNAVAILABLE")  # Driver text can carry credentials and paths.
        return ENVIRONMENT
    except OSError:
        print("STORAGE_UNAVAILABLE")
        return ENVIRONMENT


if __name__ == "__main__":
    sys.exit(main())
