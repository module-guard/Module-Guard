"""Scan commitments and the append-only, file-based transparency log.

Layout under `<snapshot_root>/transparency/` (a bundle written by `export` has the same one):

    leaves.jsonl                       one canonical JSON entry per line, appended with O_APPEND
    checkpoints/<tree_size:020d>.json  signed-by-nobody tree heads, published once, never replaced
    anchors/<tree_size:020d>-<type>.json
    .lock                              exclusive, non-blocking: a second writer fails at once

The storage follows `attestations/store.py` (private modes, O_NOFOLLOW, regular files only,
temp file + fsync + link) without importing it. Error messages are fixed text: no stored
content, path or external error text ever reaches a caller.
"""

import fcntl
import os
import re
import stat
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID, uuid4

from sqlalchemy import select

from app.attestations.signing import canonical, strict_json
from app.auth.visibility import repository_visible_to
from app.core.exceptions import ConflictError, DomainError
from app.models import ModuleVersion, Repository, Scan
from app.models.enums import ScanStatus
from app.services.attestation_service import result_export_digest
from app.storage.integrity import digest
from app.transparency import merkle

FORMAT = 1
MAX_LINE = 4096
MAX_ENTRIES = 1_000_000
MAX_LOG_BYTES = 256 * 1024 * 1024
MAX_REASON = 200
MAX_SMALL_FILE = 64 * 1024
MAX_ANCHOR_FILE = 1024 * 1024
MAX_FILES = 100_000
ANCHOR_TYPES = ("opentimestamps", "rfc3161")
LOGGED = (ScanStatus.COMPLETED, ScanStatus.FAILED)

_HEX = re.compile(r"[0-9a-f]{64}")
_CHECKPOINT = re.compile(r"\d{20}\.json")
_ANCHOR = re.compile(r"(\d{20})-(opentimestamps|rfc3161)\.json")
_ENTRY_KEYS = {"format", "seq", "kind", "reason", "recorded_at", "commitment"}
_COMMITMENT_KEYS = {
    "scan_id",
    "module_version_id",
    "status",
    "result_sha256",
    "manifest_sha256",
    "snapshot_sha256",
}
_CHECKPOINT_KEYS = {"format", "tree_size", "root_hash", "created_at", "previous"}
_ANCHOR_KEYS = {
    "type",
    "status",
    "tree_size",
    "root_hash",
    "receipts_b64",
    "receipt_sha256",
    "created_at",
    "code",
}


class LogUnavailable(DomainError):
    """The store cannot be read or written (environment problem, not evidence of tampering)."""

    status_code = 503
    code = "LOG_UNAVAILABLE"

    def __init__(self, reason="투명성 로그를 사용할 수 없습니다."):
        super().__init__(reason)


class LogInconsistent(DomainError):
    """The stored log contradicts itself: evidence of modification or corruption."""

    status_code = 503
    code = "LOG_INCONSISTENT"

    def __init__(self):
        super().__init__("투명성 로그가 일관되지 않습니다.")


class LogBusy(DomainError):
    status_code = 409
    code = "LOG_BUSY"

    def __init__(self):
        super().__init__("다른 프로세스가 투명성 로그에 기록 중입니다.")


class NotLogged(Exception):
    """A scan whose commitment cannot be computed. `code` is a fixed word, never result text."""

    def __init__(self, code):
        self.code = code
        super().__init__(code)


# ------------------------------------------------------------------ commitments


def current_commitment(session, scan) -> dict:
    """What the database says about one scan right now (format 1 leaf commitment)."""
    try:
        result = result_export_digest(session, scan)
    except ConflictError:
        raise NotLogged("TOO_LARGE") from None
    except ValueError:
        raise NotLogged("RESULT_INVALID") from None
    version = session.get(ModuleVersion, scan.module_version_id) if scan.module_version_id else None
    return {
        "scan_id": str(scan.id),
        "module_version_id": str(scan.module_version_id) if scan.module_version_id else None,
        "status": scan.status.value,
        "result_sha256": result,
        "manifest_sha256": version.manifest_sha256 if version else None,
        "snapshot_sha256": version.snapshot_sha256 if version else None,
    }


def commitment_sha256(commitment: dict) -> str:
    return digest(canonical(commitment))


def entry_leaf(entry: dict) -> bytes:
    return merkle.leaf_hash(canonical(entry))


def _is_hex(value) -> bool:
    return isinstance(value, str) and _HEX.fullmatch(value) is not None


def _is_uuid(value) -> bool:
    try:
        return isinstance(value, str) and str(UUID(value)) == value
    except ValueError:
        return False


def _is_time(value) -> bool:
    try:
        return isinstance(value, str) and datetime.fromisoformat(value).tzinfo is not None
    except ValueError:
        return False


def _is_size(value, minimum=0) -> bool:
    return type(value) is int and minimum <= value <= 2**63


def _entry_ok(entry, seq) -> bool:
    if not isinstance(entry, dict) or set(entry) != _ENTRY_KEYS:
        return False
    commitment, kind, reason = entry["commitment"], entry["kind"], entry["reason"]
    if not (entry["format"] == FORMAT and type(entry["seq"]) is int and entry["seq"] == seq):
        return False
    if kind == "SCAN":
        if reason is not None:
            return False
    elif kind != "REVISION" or not isinstance(reason, str) or not 0 < len(reason) <= MAX_REASON:
        return False
    if not _is_time(entry["recorded_at"]):
        return False
    if not isinstance(commitment, dict) or set(commitment) != _COMMITMENT_KEYS:
        return False
    version = commitment["module_version_id"]
    return (
        _is_uuid(commitment["scan_id"])
        and (version is None or _is_uuid(version))
        and commitment["status"] in ("COMPLETED", "FAILED")
        and _is_hex(commitment["result_sha256"])
        and all(
            commitment[k] is None or _is_hex(commitment[k])
            for k in ("manifest_sha256", "snapshot_sha256")
        )
    )


def _check_entry(entry, seq):
    if not _entry_ok(entry, seq):
        raise LogInconsistent


def _check_checkpoint(data, name=None):
    valid = (
        isinstance(data, dict)
        and set(data) == _CHECKPOINT_KEYS
        and data["format"] == FORMAT
        and _is_size(data["tree_size"], 1)
        and _is_hex(data["root_hash"])
        and _is_time(data["created_at"])
        and (
            data["previous"] is None
            or (
                isinstance(data["previous"], dict)
                and set(data["previous"]) == {"tree_size", "root_hash"}
                and _is_size(data["previous"]["tree_size"], 1)
                and _is_hex(data["previous"]["root_hash"])
            )
        )
        and (name is None or int(name) == data["tree_size"])
    )
    if not valid:
        raise LogInconsistent


def _check_anchor(data, size, kind):
    receipts = data.get("receipts_b64") if isinstance(data, dict) else None
    valid = (
        isinstance(data, dict)
        and set(data) <= _ANCHOR_KEYS
        and {"type", "status", "tree_size", "root_hash", "receipts_b64", "created_at"} <= set(data)
        and data["type"] == kind
        and data["tree_size"] == size
        and data["status"] in ("submitted", "failed")
        and _is_hex(data["root_hash"])
        and _is_time(data["created_at"])
        and isinstance(receipts, list)
        and all(isinstance(r, str) for r in receipts)
        and (data["status"] == "failed" or (receipts and _is_hex(data.get("receipt_sha256"))))
        and ("code" not in data or isinstance(data["code"], str))
    )
    if not valid:
        raise LogInconsistent


def receipt_sha256(receipts_b64: list[str]) -> str:
    return digest(canonical(receipts_b64))


# ------------------------------------------------------------------ reading


@dataclass
class LogState:
    entries: list[dict] = field(default_factory=list)
    hashes: list[bytes] = field(default_factory=list)
    checkpoints: list[dict] = field(default_factory=list)
    anchors: dict[tuple[int, str], dict] = field(default_factory=dict)
    # scan_id -> index of its newest entry; the expected commitment is that entry's.
    last: dict[str, int] = field(default_factory=dict)
    revisions: dict[str, int] = field(default_factory=dict)
    size: int = 0  # bytes of leaves.jsonl
    exists: bool = False

    @property
    def tree_head(self) -> dict | None:
        return self.checkpoints[-1] if self.checkpoints else None

    def append(self, entry: dict, length: int):
        scan_id = entry["commitment"]["scan_id"]
        self.entries.append(entry)
        self.hashes.append(entry_leaf(entry))
        self.last[scan_id] = entry["seq"]
        if entry["kind"] == "REVISION":
            self.revisions[scan_id] = self.revisions.get(scan_id, 0) + 1
        self.size += length


def _read_file(fd, name, cap):
    try:
        handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
    except FileNotFoundError:
        return None
    except OSError:
        raise LogUnavailable from None
    with os.fdopen(handle, "rb") as stream:
        # Links, pipes and devices are never log files.
        if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
            raise LogUnavailable
        data = stream.read(cap + 1)
    if len(data) > cap:
        raise LogUnavailable
    return data


def _parse(data):
    try:
        return strict_json(data)
    except (ValueError, RecursionError):
        raise LogInconsistent from None


def _names(fd, pattern):
    try:
        names = sorted(name for name in os.listdir(fd) if pattern.fullmatch(name))
    except OSError:
        raise LogUnavailable from None
    if len(names) > MAX_FILES:
        raise LogUnavailable
    return names


def _read_leaves(fd, state):
    data = _read_file(fd, "leaves.jsonl", MAX_LOG_BYTES)
    if data is None:
        return
    state.exists = True
    if data and not data.endswith(b"\n"):
        raise LogInconsistent  # A torn final line: never guess what it was meant to say.
    lines = data.split(b"\n")[:-1]
    if len(lines) > MAX_ENTRIES:
        raise LogUnavailable
    for seq, line in enumerate(lines):
        if len(line) > MAX_LINE:
            raise LogInconsistent
        entry = _parse(line)
        _check_entry(entry, seq)
        # Exactly the bytes the leaf hash was taken over: no reformatting of a stored line.
        if canonical(entry) != line:
            raise LogInconsistent
        scan_id = entry["commitment"]["scan_id"]
        # A scan starts with SCAN; only later entries for it may be REVISION.
        if (entry["kind"] == "SCAN") == (scan_id in state.last):
            raise LogInconsistent
        state.append(entry, len(line) + 1)


def _read_checkpoints(fd, state, full):
    previous = None
    for name in _names(fd, _CHECKPOINT):
        data = _read_file(fd, name, MAX_SMALL_FILE)
        if data is None:
            raise LogInconsistent
        checkpoint = _parse(data)
        _check_checkpoint(checkpoint, name[:20])
        expected = (
            None
            if previous is None
            else {"tree_size": previous["tree_size"], "root_hash": previous["root_hash"]}
        )
        if checkpoint["previous"] != expected or checkpoint["tree_size"] > len(state.entries):
            raise LogInconsistent
        state.checkpoints.append(checkpoint)
        previous = checkpoint
    # The newest head must be reproducible from the entries; `verify` checks every older one.
    for checkpoint in state.checkpoints if full else state.checkpoints[-1:]:
        if merkle.root(state.hashes, checkpoint["tree_size"]).hex() != checkpoint["root_hash"]:
            raise LogInconsistent


def _read_anchors(fd, state):
    sizes = {c["tree_size"] for c in state.checkpoints}
    for name in _names(fd, _ANCHOR):
        size, kind = _ANCHOR.fullmatch(name).groups()
        data = _read_file(fd, name, MAX_ANCHOR_FILE)
        if data is None:
            raise LogInconsistent
        record = _parse(data)
        _check_anchor(record, int(size), kind)
        if int(size) not in sizes:
            raise LogInconsistent
        state.anchors[(int(size), kind)] = record


def _read_state(dirs, full=False) -> LogState:
    state = LogState()
    if dirs is None:
        return state
    root_fd, checkpoints_fd, anchors_fd = dirs
    _read_leaves(root_fd, state)
    if checkpoints_fd is not None:
        _read_checkpoints(checkpoints_fd, state, full)
    if anchors_fd is not None:
        _read_anchors(anchors_fd, state)
    return state


# ------------------------------------------------------------------ storage


def _publish(fd, name, data, *, replace=False):
    """Temp file, fsync, then link (never overwrites). Only a failed anchor is replaced."""
    temporary = f".incoming-{uuid4()}"
    try:
        handle = os.open(
            temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd
        )
        with os.fdopen(handle, "wb") as stream:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
        if replace:
            os.replace(temporary, name, src_dir_fd=fd, dst_dir_fd=fd)
        else:
            os.link(temporary, name, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
        os.fsync(fd)
    except FileExistsError:
        raise LogInconsistent from None
    except OSError:
        raise LogUnavailable from None
    finally:
        try:
            os.unlink(temporary, dir_fd=fd)
        except OSError:
            pass


def _now() -> str:
    return datetime.now(UTC).isoformat()


class Writer:
    """Holds the lock. The state is read once, then kept in step with every write."""

    def __init__(self, dirs, state):
        self.dirs, self.state, self._leaves = dirs, state, None

    def _leaves_fd(self):
        if self._leaves is None:
            try:
                fd = os.open(
                    "leaves.jsonl",
                    os.O_WRONLY | os.O_APPEND | os.O_CREAT | os.O_NOFOLLOW,
                    0o600,
                    dir_fd=self.dirs[0],
                )
            except OSError:
                raise LogUnavailable from None
            info = os.fstat(fd)
            # Something grew or replaced the file after it was read under this lock.
            if not stat.S_ISREG(info.st_mode) or info.st_size != self.state.size:
                os.close(fd)
                raise LogInconsistent
            self._leaves = fd
        return self._leaves

    def add(self, kind, commitment, reason=None):
        if len(self.state.entries) >= MAX_ENTRIES:
            raise LogUnavailable
        if reason is not None and not 0 < len(reason) <= MAX_REASON:
            raise LogUnavailable("사유는 1자 이상 200자 이하여야 합니다.")
        entry = {
            "format": FORMAT,
            "seq": len(self.state.entries),
            "kind": kind,
            "reason": reason,
            "recorded_at": _now(),
            "commitment": commitment,
        }
        line = canonical(entry)
        if len(line) > MAX_LINE:
            raise LogUnavailable("투명성 로그 항목이 너무 큽니다.")
        data = line + b"\n"
        fd = self._leaves_fd()
        try:
            # One write call per line: concurrent readers see whole lines only.
            if os.write(fd, data) != len(data):
                raise LogInconsistent
        except OSError:
            raise LogUnavailable from None
        self.state.append(entry, len(data))
        return entry

    def sync(self):
        if self._leaves is not None:
            try:
                os.fsync(self._leaves)
                os.fsync(self.dirs[0])
            except OSError:
                raise LogUnavailable from None

    def checkpoint(self):
        """Publish a tree head if entries were added since the last one. Returns it or None."""
        state = self.state
        size = len(state.entries)
        head = state.tree_head
        if size == 0 or (head is not None and head["tree_size"] >= size):
            return None
        self.sync()
        checkpoint = {
            "format": FORMAT,
            "tree_size": size,
            "root_hash": merkle.root(state.hashes, size).hex(),
            "created_at": _now(),
            "previous": None
            if head is None
            else {"tree_size": head["tree_size"], "root_hash": head["root_hash"]},
        }
        _publish(self.dirs[1], f"{size:020d}.json", canonical(checkpoint) + b"\n")
        state.checkpoints.append(checkpoint)
        return checkpoint

    def put_anchor(self, record) -> bool:
        """Store an anchor result. A submitted anchor is final; a failed one may be retried."""
        key = (record["tree_size"], record["type"])
        existing = self.state.anchors.get(key)
        if existing is not None and existing["status"] == "submitted":
            return False
        data = canonical(record) + b"\n"
        if len(data) > MAX_ANCHOR_FILE:
            raise LogUnavailable
        _publish(
            self.dirs[2],
            f"{record['tree_size']:020d}-{record['type']}.json",
            data,
            replace=existing is not None,
        )
        self.state.anchors[key] = record
        return True


class TransparencyLog:
    def __init__(self, root: Path):
        self.root = Path(root)

    @contextmanager
    def _open(self, create=False):
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        fds = []
        try:
            if create:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                root_fd = os.open(self.root, flags)
            except FileNotFoundError:
                yield None  # No log yet: reading is not an error, it is an empty log.
                return
            fds.append(root_fd)
            subdirectories = []
            for name in ("checkpoints", "anchors"):
                if create:
                    try:
                        os.mkdir(name, mode=0o700, dir_fd=root_fd)
                    except FileExistsError:
                        pass
                try:
                    subdirectories.append(os.open(name, flags, dir_fd=root_fd))
                    fds.append(subdirectories[-1])
                except FileNotFoundError:
                    subdirectories.append(None)
            yield root_fd, *subdirectories
        except OSError:
            raise LogUnavailable from None
        finally:
            for fd in fds:
                os.close(fd)

    def read(self, *, full=False) -> LogState:
        with self._open() as dirs:
            return _read_state(dirs, full)

    @contextmanager
    def writing(self):
        with self._open(create=True) as dirs:
            try:
                lock = os.open(
                    ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=dirs[0]
                )
            except OSError:
                raise LogUnavailable from None
            writer = None
            try:
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise LogBusy from None
                writer = Writer(dirs, _read_state(dirs, full=False))
                yield writer
            finally:
                if writer is not None and writer._leaves is not None:
                    os.close(writer._leaves)
                os.close(lock)

    def export(self, destination: Path):
        """Write a verifiable bundle of the (fully checked) log to a new, empty directory."""
        state = self.read(full=True)
        destination = Path(destination)
        try:
            occupied = destination.exists() and any(destination.iterdir())
        except OSError:
            raise LogUnavailable from None
        if occupied:
            raise LogUnavailable("내보낼 디렉터리가 비어 있지 않습니다.")
        bundle = TransparencyLog(destination)
        with bundle._open(create=True) as dirs:
            if dirs is None:
                raise LogUnavailable
            data = b"".join(canonical(e) + b"\n" for e in state.entries)
            _publish(dirs[0], "leaves.jsonl", data)
            for checkpoint in state.checkpoints:
                _publish(
                    dirs[1], f"{checkpoint['tree_size']:020d}.json", canonical(checkpoint) + b"\n"
                )
            for (size, kind), record in state.anchors.items():
                _publish(dirs[2], f"{size:020d}-{kind}.json", canonical(record) + b"\n")
        return state


# ------------------------------------------------------------------ batch operations


def append_scans(session, writer) -> tuple[int, dict[str, str]]:
    """Add every finished scan that has no entry yet. Returns (appended, {scan_id: code})."""
    ids = session.scalars(
        select(Scan.id)
        .where(Scan.status.in_(LOGGED), Scan.finished_at.is_not(None))
        .order_by(Scan.finished_at, Scan.id)
    ).all()
    appended, skipped = 0, {}
    for scan_id in ids:
        if str(scan_id) in writer.state.last:
            continue
        scan = session.get(Scan, scan_id)
        try:
            commitment = current_commitment(session, scan)
        except NotLogged as error:
            skipped[str(scan_id)] = error.code
            continue
        finally:
            session.expunge_all()
        writer.add("SCAN", commitment)
        appended += 1
    writer.sync()
    return appended, skipped


def changed_scans(session, state, scan_ids=None) -> tuple[list[tuple[str, dict]], dict[str, str]]:
    """Logged scans whose current commitment differs from their newest entry."""
    wanted = {str(i) for i in scan_ids} if scan_ids else None
    changed, skipped = [], {}
    for scan_id, index in state.last.items():
        if wanted is not None and scan_id not in wanted:
            continue
        scan = session.get(Scan, UUID(scan_id))
        if scan is None:
            continue
        try:
            commitment = current_commitment(session, scan)
        except NotLogged as error:
            skipped[scan_id] = error.code
            continue
        finally:
            session.expunge_all()
        if commitment != state.entries[index]["commitment"]:
            changed.append((scan_id, commitment))
    return changed, skipped


def record_revisions(writer, reason, changed) -> int:
    for _, commitment in changed:
        writer.add("REVISION", commitment, reason)
    writer.sync()
    return len(changed)


# ------------------------------------------------------------------ verification


def check_log(state, trusted=None) -> list[str]:
    """Checks that need no database: adjacent checkpoints, anchors and a trusted head."""
    findings = []
    heads = state.checkpoints
    for before, after in zip(heads, heads[1:], strict=False):
        proof = merkle.consistency_proof(state.hashes, before["tree_size"], after["tree_size"])
        if not merkle.verify_consistency(
            before["tree_size"],
            after["tree_size"],
            bytes.fromhex(before["root_hash"]),
            bytes.fromhex(after["root_hash"]),
            proof,
        ):
            findings.append("CONSISTENCY_FAILED")
            break
    by_size = {c["tree_size"]: c for c in heads}
    for (size, _), record in sorted(state.anchors.items()):
        if record["root_hash"] != by_size[size]["root_hash"] or (
            record["status"] == "submitted"
            and record["receipt_sha256"] != receipt_sha256(record["receipts_b64"])
        ):
            findings.append("ANCHOR_MISMATCH")
            break
    if trusted is not None:
        newest = state.tree_head
        if newest is None or trusted["tree_size"] > newest["tree_size"]:
            findings.append("CONSISTENCY_FAILED")  # Rolled back below what was already seen.
        elif not merkle.verify_consistency(
            trusted["tree_size"],
            newest["tree_size"],
            bytes.fromhex(trusted["root_hash"]),
            bytes.fromhex(newest["root_hash"]),
            merkle.consistency_proof(state.hashes, trusted["tree_size"], newest["tree_size"]),
        ):
            findings.append("CONSISTENCY_FAILED")
    return list(dict.fromkeys(findings))


def load_trusted(path: Path) -> dict:
    """A checkpoint file the verifier kept from an earlier run."""
    try:
        with Path(path).open("rb") as stream:
            data = stream.read(MAX_SMALL_FILE + 1)
    except OSError:
        raise LogUnavailable from None
    if len(data) > MAX_SMALL_FILE:
        raise LogUnavailable
    trusted = _parse(data)
    _check_checkpoint(trusted)
    return trusted


def reconcile(session, state) -> dict:
    """Compare the database with the newest entry of every scan."""
    result = {"ok": 0, "pending": 0, "mismatch": [], "missing": [], "not_logged": []}
    head = state.tree_head["tree_size"] if state.tree_head else 0
    for scan_id in state.last:
        if session.get(Scan, UUID(scan_id)) is None:
            result["missing"].append(scan_id)
        session.expunge_all()
    ids = session.scalars(
        select(Scan.id)
        .where(Scan.status.in_(LOGGED), Scan.finished_at.is_not(None))
        .order_by(Scan.finished_at, Scan.id)
    ).all()
    for scan_id in ids:
        key = str(scan_id)
        index = state.last.get(key)
        if index is None:
            result["not_logged"].append(key)
            continue
        scan = session.get(Scan, scan_id)
        try:
            same = current_commitment(session, scan) == state.entries[index]["commitment"]
        except NotLogged:
            same = False
        finally:
            session.expunge_all()
        if not same:
            result["mismatch"].append(key)
        elif index >= head:
            result["pending"] += 1
        else:
            result["ok"] += 1
    return result


# ------------------------------------------------------------------ one scan (API)


def shown_to(session, entry, version_id, viewer_id) -> bool:
    """Whether the raw entry may be shown.

    Forks share a scan, so an entry can name the original's version. That id is shown only
    to a viewer who may see the repository it belongs to (or who asked about it): otherwise
    the response would reveal that a hidden original exists.
    """
    logged = entry["commitment"]["module_version_id"]
    if logged is None or logged == str(version_id):
        return True
    return (
        session.scalar(
            select(ModuleVersion.id)
            .join(Repository, Repository.id == ModuleVersion.repository_id)
            .where(ModuleVersion.id == UUID(logged), repository_visible_to(viewer_id))
        )
        is not None
    )


def describe_scan(session, scan, state, *, show_entry=True) -> dict:
    reasons, current = [], None
    if scan.status not in LOGGED:
        reasons.append("SCAN_NOT_FINISHED")
    else:
        try:
            current = current_commitment(session, scan)
        except NotLogged as error:
            reasons.append(error.code)
    result = {
        "schema_version": 1,
        "scan_id": str(scan.id),
        "status": "not_logged",
        "reasons": reasons,
        "leaf_index": None,
        "entry": None,
        "leaf_hash": None,
        "current_commitment_sha256": commitment_sha256(current) if current else None,
        "logged_commitment_sha256": None,
        "revisions": 0,
        "checkpoint": None,
        "inclusion_proof": [],
    }
    index = state.last.get(str(scan.id))
    if index is None:
        return result
    entry = state.entries[index]
    logged = commitment_sha256(entry["commitment"])
    head = state.tree_head
    included = head is not None and head["tree_size"] > index
    changed = current is None or commitment_sha256(current) != logged
    if changed:
        reasons.append("COMMITMENT_MISMATCH")
    if not included:
        reasons.append("NOT_IN_CHECKPOINT")
    result.update(
        status="mismatch" if changed else "included" if included else "pending",
        reasons=reasons,
        leaf_index=index,
        entry=entry if show_entry else None,
        leaf_hash=state.hashes[index].hex(),
        logged_commitment_sha256=logged,
        revisions=state.revisions.get(str(scan.id), 0),
    )
    if included:
        result["checkpoint"] = {"tree_size": head["tree_size"], "root_hash": head["root_hash"]}
        result["inclusion_proof"] = [
            p.hex() for p in merkle.inclusion_proof(state.hashes, index, head["tree_size"])
        ]
    return result
