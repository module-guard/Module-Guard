"""Private, append-only proof files, separate from mutable/shared scan JSON."""

import fcntl
import os
import re
import time
from contextlib import contextmanager
from pathlib import Path
from uuid import UUID, uuid4

from app.attestations.signing import AttestationUnavailable, canonical, strict_json
from app.core.exceptions import ConflictError

MAX_ENTRIES = 16
MAX_RECORD_BYTES = 270336
_NAME = re.compile(r"\d{20}-[0-9a-f-]{36}\.json")


class ProofStore:
    def __init__(self, root: Path):
        self.root = root

    @contextmanager
    def directory(self, version_id, *, create=False):
        version_id = str(UUID(str(version_id)))
        root_fd = version_fd = None
        flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        try:
            if create:
                self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
            root_fd = os.open(self.root, flags)
            if create:
                try:
                    os.mkdir(version_id, mode=0o700, dir_fd=root_fd)
                except FileExistsError:
                    pass
            version_fd = os.open(version_id, flags, dir_fd=root_fd)
        except FileNotFoundError:
            if create:
                if root_fd is not None:
                    os.close(root_fd)
                raise AttestationUnavailable("증명 저장소를 사용할 수 없습니다.") from None
        except OSError:
            if root_fd is not None:
                os.close(root_fd)
            raise AttestationUnavailable("증명 저장소를 사용할 수 없습니다.") from None
        try:
            yield version_fd
        finally:
            if version_fd is not None:
                os.close(version_fd)
            if root_fd is not None:
                os.close(root_fd)

    def _read(self, fd):
        if fd is None:
            return []
        try:
            names = sorted(name for name in os.listdir(fd) if _NAME.fullmatch(name))
        except OSError:
            raise AttestationUnavailable("증명 저장소를 읽을 수 없습니다.") from None
        if len(names) > MAX_ENTRIES:
            raise AttestationUnavailable("증명 저장소 상한을 초과했습니다.")
        records = []
        try:
            for name in names:
                handle = os.open(name, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK, dir_fd=fd)
                with os.fdopen(handle, "rb") as stream:
                    # Named pipes/devices are never valid proof files.
                    import stat

                    if not stat.S_ISREG(os.fstat(stream.fileno()).st_mode):
                        raise ValueError("Not a regular proof file")
                    data = stream.read(MAX_RECORD_BYTES + 1)
                if len(data) > MAX_RECORD_BYTES:
                    raise ValueError("Proof too large")
                records.append(strict_json(data))
        except (OSError, ValueError, RecursionError):
            raise AttestationUnavailable("증명 저장소를 읽을 수 없습니다.") from None
        return records

    def read(self, version_id):
        with self.directory(version_id) as fd:
            return self._read(fd)

    @contextmanager
    def issuance(self, version_id):
        with self.directory(version_id, create=True) as fd:
            try:
                lock = os.open(".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600, dir_fd=fd)
            except OSError:
                raise AttestationUnavailable("증명 저장소를 잠글 수 없습니다.") from None
            try:
                # Avoid leaving a request stuck behind another signer/network call.
                try:
                    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ConflictError(
                        "이미 증명 발급 중입니다. 잠시 뒤 다시 시도해 주세요."
                    ) from None
                if len(self._read(fd)) >= MAX_ENTRIES:
                    raise ConflictError("버전당 증명 발급 상한에 도달했습니다.")
                yield fd
            finally:
                os.close(lock)

    def append(self, fd, proof):
        identifier = uuid4()
        data = canonical({"id": str(identifier), "proof": proof.model_dump(mode="json")})
        if len(data) > MAX_RECORD_BYTES:
            raise AttestationUnavailable("증명 크기 상한을 초과했습니다.")
        temporary = f".incoming-{identifier}"
        final = f"{time.time_ns():020d}-{identifier}.json"
        try:
            handle = os.open(
                temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600, dir_fd=fd
            )
            with os.fdopen(handle, "wb") as stream:
                stream.write(data)
                stream.flush()
                os.fsync(stream.fileno())
            os.link(temporary, final, src_dir_fd=fd, dst_dir_fd=fd, follow_symlinks=False)
            os.fsync(fd)
        except OSError:
            raise AttestationUnavailable("증명을 저장할 수 없습니다.") from None
        finally:
            try:
                os.unlink(temporary, dir_fd=fd)
            except FileNotFoundError:
                pass
