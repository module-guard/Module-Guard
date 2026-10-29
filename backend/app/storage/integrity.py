"""Version 1 manifest: sorted POSIX paths, UTF-8 canonical JSON, exact file bytes.

Git metadata, Terraform caches and the existing snapshot secret exclusions are outside
this manifest's scope. No links or special files are accepted, including in directories.
"""

import hashlib
import json
import os
import stat
from pathlib import Path

from app.core.exceptions import DomainError


class IntegrityError(DomainError):
    status_code = 409

    def __init__(self, status="MISMATCH", paths=None):
        self.status = status
        self.code = f"Integrity{status.title()}"
        self.details = [{"path": p} for p in (paths or [])[:1000]]
        super().__init__("모듈 무결성을 확인할 수 없어 분석 또는 다운로드를 차단했습니다.")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def manifest_bytes(manifest: dict) -> bytes:
    return json.dumps(manifest, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode(
        "utf-8"
    )


def manifest_for(files: dict[str, tuple[bytes, int]]) -> dict:
    return {
        "format": 1,
        "files": [
            {"path": name, "sha256": digest(data), "size": len(data), "mode": mode}
            for name, (data, mode) in sorted(files.items())
        ],
    }


def compare(expected: dict | None, actual: dict, expected_hash: str | None = None):
    if expected is None:
        raise IntegrityError("UNVERIFIED")
    if expected_hash is not None and digest(manifest_bytes(expected)) != expected_hash:
        raise IntegrityError()
    before = {f["path"]: f for f in expected["files"]}
    after = {f["path"]: f for f in actual["files"]}
    changed = sorted(p for p in before.keys() | after.keys() if before.get(p) != after.get(p))
    if expected.get("format") != actual.get("format") or changed:
        raise IntegrityError(paths=changed)


def read_tree(root: Path, max_bytes: int) -> dict[str, tuple[bytes, int]]:
    # Open every component relative to a pinned directory descriptor. A symlink swap
    # cannot redirect a read outside this tree. Metadata detects writes during a read.
    from app.storage.snapshots import EXCLUDED_DIRECTORIES, is_excluded

    files = {}
    total = 0
    flags = os.O_RDONLY | os.O_NOFOLLOW

    def visit(fd, prefix):
        nonlocal total
        for name in sorted(os.listdir(fd)):
            path = f"{prefix}/{name}" if prefix else name
            if "\\" in name or ":" in name or name in {".", ".."}:
                raise IntegrityError("ERROR")
            info = os.stat(name, dir_fd=fd, follow_symlinks=False)
            if stat.S_ISLNK(info.st_mode):
                raise IntegrityError("ERROR")
            if stat.S_ISDIR(info.st_mode):
                if name in EXCLUDED_DIRECTORIES:
                    continue
                child = os.open(name, flags | os.O_DIRECTORY, dir_fd=fd)
                try:
                    visit(child, path)
                finally:
                    os.close(child)
            elif stat.S_ISREG(info.st_mode):
                if is_excluded(name):
                    continue
                child = os.open(name, flags | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(child, "rb") as stream:
                    before = os.fstat(stream.fileno())
                    if not stat.S_ISREG(before.st_mode):
                        raise IntegrityError("ERROR")
                    data = stream.read(max_bytes - total + 1)
                    after = os.fstat(stream.fileno())
                if (before.st_ino, before.st_size, before.st_mtime_ns, before.st_ctime_ns) != (
                    after.st_ino,
                    after.st_size,
                    after.st_mtime_ns,
                    after.st_ctime_ns,
                ):
                    raise IntegrityError(paths=[path])
                total += len(data)
                if total > max_bytes or len(files) >= 100000:
                    raise IntegrityError("ERROR")
                files[path] = (data, 0o755 if before.st_mode & 0o111 else 0o644)
            else:
                raise IntegrityError("ERROR")

    try:
        fd = os.open(root, flags | os.O_DIRECTORY)
        try:
            visit(fd, "")
        finally:
            os.close(fd)
    except (OSError, UnicodeError):
        raise IntegrityError("ERROR") from None
    return files


def verify_tree(root: Path, manifest: dict, max_bytes: int, sha256: str | None = None):
    compare(manifest, manifest_for(read_tree(root, max_bytes)), sha256)
