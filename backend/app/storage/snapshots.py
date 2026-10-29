"""Content-addressed storage for collected module sources."""

import gzip
import hashlib
import io
import os
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import RepositoryTooLargeError, UnsafeRepositoryError
from app.storage.integrity import (
    IntegrityError,
    compare,
    digest,
    manifest_bytes,
    manifest_for,
    read_tree,
)

EXCLUDED_DIRECTORIES = {".git", ".terraform"}
# Files that commonly hold real credentials are never stored; *.tfvars.example is kept.
EXCLUDED_SUFFIXES = (
    ".tfstate",
    ".tfstate.backup",
    ".tfvars",
    ".tfvars.json",
    ".pem",
    ".key",
    ".p12",
)
EXCLUDED_PREFIXES = (".env", "id_rsa")


def is_excluded(name: str) -> bool:
    lowered = name.lower()
    return (
        ".tfstate" in lowered
        or lowered.endswith(EXCLUDED_SUFFIXES)
        or lowered.startswith(EXCLUDED_PREFIXES)
    )


@dataclass(frozen=True)
class SnapshotInfo:
    sha256: str
    size_bytes: int
    file_count: int
    manifest: dict
    manifest_sha256: str


def snapshot_files(root: Path) -> list[Path]:
    """Regular files to keep, sorted by relative path. Symlinks are never followed or stored."""
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            if (Path(directory) / name).is_symlink():
                raise UnsafeRepositoryError(
                    "Repositories containing symbolic links are not supported."
                )
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRECTORIES]
        for name in names:
            entry = Path(directory) / name
            if not entry.is_file():
                raise UnsafeRepositoryError("Repository contains a non-regular file.")
            if is_excluded(name):
                continue
            files.append(entry)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


class SnapshotStore:
    """Stores each distinct source tree once as {root}/{sha256}.tar.gz.

    Archives are deterministic (sorted entries, zeroed timestamps and owners, normalised modes),
    so identical content always produces the same digest and the same file.
    """

    def __init__(self, root: Path, max_bytes: int):
        self.root = root
        self.max_bytes = max_bytes

    def path(self, sha256: str) -> Path:
        if len(sha256) != 64 or any(c not in "0123456789abcdef" for c in sha256):
            raise ValueError("Invalid snapshot digest.")
        return self.root / f"{sha256}.tar.gz"

    def save(self, source: Path) -> SnapshotInfo:
        source = source.resolve(strict=True)
        files = snapshot_files(source)
        if sum(path.stat().st_size for path in files) > self.max_bytes:
            raise RepositoryTooLargeError("Repository exceeds the snapshot size limit.")
        frozen = read_tree(source, self.max_bytes)
        manifest = manifest_for(frozen)
        compare(manifest, manifest_for(read_tree(source, self.max_bytes)))
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=self.root, prefix=".incoming-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
                    ) as archive:
                        for name, (data, mode) in sorted(frozen.items()):
                            info = tarfile.TarInfo(name)
                            info.size = len(data)
                            info.mode = mode
                            archive.addfile(info, io.BytesIO(data))
            digest = hashlib.sha256()
            with open(temporary, "rb") as written:
                for chunk in iter(lambda: written.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha256 = digest.hexdigest()
            target = self.path(sha256)
            size = os.path.getsize(temporary)
            if target.exists():
                if hashlib.sha256(target.read_bytes()).hexdigest() != sha256:
                    raise IntegrityError()
                os.unlink(temporary)
            else:
                os.replace(temporary, target)
            return SnapshotInfo(
                sha256,
                size,
                len(files),
                manifest,
                hashlib.sha256(manifest_bytes(manifest)).hexdigest(),
            )
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

    def verified_bytes(self, sha256: str, manifest: dict, manifest_sha256: str) -> bytes:
        if not manifest or not manifest_sha256:
            raise IntegrityError("UNVERIFIED")
        try:
            with self.path(sha256).open("rb") as source:
                data = source.read(self.max_bytes * 2 + 1024 * 1024 + 1)
            if digest(data) != sha256:
                raise IntegrityError()
            files = {}
            total = 0
            from app.usage.archive import _safe_member

            with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
                for member in archive:
                    if not member.isfile() or not _safe_member(member.name) or member.name in files:
                        raise IntegrityError()
                    total += member.size
                    if total > self.max_bytes or len(files) >= 100000:
                        raise IntegrityError("ERROR")
                    content = archive.extractfile(member)
                    files[member.name] = (content.read(), 0o755 if member.mode & 0o111 else 0o644)
            compare(manifest, manifest_for(files), manifest_sha256)
            return data
        except IntegrityError:
            raise
        except (OSError, ValueError, tarfile.TarError, EOFError):
            raise IntegrityError("ERROR") from None

    def stored(self) -> dict[str, float]:
        """Digest -> modification time of every snapshot on disk."""
        if not self.root.is_dir():
            return {}
        return {
            path.name.removesuffix(".tar.gz"): path.stat().st_mtime
            for path in self.root.glob("*.tar.gz")
        }

    def remove(self, sha256: str) -> None:
        """Delete one snapshot file. Callers must have applied the retention rule first."""
        self.path(sha256).unlink(missing_ok=True)
