"""Content-addressed storage for collected module sources."""

import gzip
import hashlib
import os
import tarfile
import tempfile
from dataclasses import dataclass
from pathlib import Path

from app.core.exceptions import RepositoryTooLargeError, UnsafeRepositoryError

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
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        handle, temporary = tempfile.mkstemp(dir=self.root, prefix=".incoming-", suffix=".tmp")
        try:
            with os.fdopen(handle, "wb") as raw:
                with gzip.GzipFile(filename="", mode="wb", fileobj=raw, mtime=0) as compressed:
                    with tarfile.open(
                        fileobj=compressed, mode="w", format=tarfile.GNU_FORMAT
                    ) as archive:
                        for path in files:
                            info = tarfile.TarInfo(path.relative_to(source).as_posix())
                            info.size = path.stat().st_size
                            info.mode = 0o755 if os.access(path, os.X_OK) else 0o644
                            with path.open("rb") as content:
                                archive.addfile(info, content)
            digest = hashlib.sha256()
            with open(temporary, "rb") as written:
                for chunk in iter(lambda: written.read(1024 * 1024), b""):
                    digest.update(chunk)
            sha256 = digest.hexdigest()
            target = self.path(sha256)
            size = os.path.getsize(temporary)
            if target.exists():
                os.unlink(temporary)
            else:
                os.replace(temporary, target)
            return SnapshotInfo(sha256, size, len(files))
        except BaseException:
            if os.path.exists(temporary):
                os.unlink(temporary)
            raise

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
