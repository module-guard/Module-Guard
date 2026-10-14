import shutil
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID

from app.core.exceptions import UnsafeRepositoryError


class WorkspaceManager:
    def __init__(self, root: Path, keep: bool = False):
        # Resolve platform aliases such as macOS /tmp once, then own this directory.
        if root.is_symlink():
            raise UnsafeRepositoryError("Workspace root must not be a symbolic link.")
        root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root = root.resolve(strict=True)
        self.keep = keep

    @contextmanager
    def create(self, scan_id: UUID):
        name = str(UUID(str(scan_id)))
        path = self.root / name
        path.mkdir(mode=0o700, exist_ok=False)
        try:
            if path.is_symlink() or path.resolve().parent != self.root:
                raise UnsafeRepositoryError("Workspace escaped its root.")
            yield path
        finally:
            if not self.keep:
                if path.is_symlink():
                    path.unlink()
                elif path.exists():
                    shutil.rmtree(path)
