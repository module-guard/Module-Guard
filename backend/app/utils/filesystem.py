import os
from pathlib import Path

from app.core.exceptions import RepositoryTooLargeError, UnsafeRepositoryError

EXCLUDED_DIRECTORIES = {".git", ".terraform", "node_modules", "vendor", ".external_modules"}


def directory_size(path: Path) -> int:
    total = 0
    if not path.exists():
        return total
    for directory, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [d for d in dirs if not (Path(directory) / d).is_symlink()]
        for name in files:
            entry = Path(directory) / name
            try:
                if not entry.is_symlink():
                    total += entry.stat().st_size
            except FileNotFoundError:
                pass  # Git can replace temporary pack files during clone.
    return total


def check_repository_size(path: Path, limit_bytes: int) -> None:
    if directory_size(path) > limit_bytes:
        raise RepositoryTooLargeError("Repository exceeds the configured size limit.")


def terraform_files(root: Path) -> list[Path]:
    root = root.resolve(strict=True)
    files = []
    for directory, dirs, names in os.walk(root, followlinks=False):
        # Reject symlinks even in excluded trees: Checkov must never follow them.
        for name in [*dirs, *names]:
            entry = Path(directory) / name
            if entry.is_symlink():
                raise UnsafeRepositoryError(
                    "Repositories containing symbolic links are not supported."
                )
        dirs[:] = [d for d in dirs if d not in EXCLUDED_DIRECTORIES]
        for name in names:
            entry = Path(directory) / name
            if not entry.is_file():
                raise UnsafeRepositoryError("Repository contains a non-regular file.")
            if name.endswith((".tf", ".tf.json")):
                files.append(entry)
    return sorted(files)
