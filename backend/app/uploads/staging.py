"""Short-lived holding area between "prepare" and "register".

A prepared source is addressed by a one-time token that only the user who prepared it can
redeem. The directory name is the token's hash, so the token itself never touches disk.
"""

import hashlib
import json
import os
import secrets
import shutil
import time
from pathlib import Path
from uuid import UUID

from app.core.exceptions import DomainError

TREE = "tree"
REPORT = "report.json"
CLAIMED = "claimed"


class StagedUploadNotFoundError(DomainError):
    status_code = 404


class StagingStore:
    def __init__(self, root: Path, ttl_seconds: int = 1800, claimed_ttl_seconds: int = 7200):
        self.root = root
        self.ttl_seconds = ttl_seconds
        self.claimed_ttl_seconds = claimed_ttl_seconds

    def _user_root(self, user_id: UUID) -> Path:
        return self.root / "users" / str(UUID(str(user_id)))

    def _slot(self, user_id: UUID, token: str) -> Path:
        return self._user_root(user_id) / hashlib.sha256(token.encode()).hexdigest()

    def open(self, user_id: UUID) -> tuple[str, Path]:
        """New empty slot; a user holds one prepared source at a time."""
        self.discard_all(user_id)
        token = secrets.token_urlsafe(32)
        slot = self._slot(user_id, token)
        slot.mkdir(parents=True, mode=0o700)
        return token, slot

    def seal(self, slot: Path, report: dict) -> float:
        """Record what was checked. Until sealed, a slot cannot be claimed."""
        expires_at = time.time() + self.ttl_seconds
        (slot / REPORT).write_text(json.dumps({**report, "expires_at": expires_at}))
        return expires_at

    def discard(self, slot: Path) -> None:
        shutil.rmtree(slot, ignore_errors=True)

    def discard_all(self, user_id: UUID) -> None:
        shutil.rmtree(self._user_root(user_id), ignore_errors=True)

    def peek(self, user_id: UUID, token: str) -> dict:
        """The sealed report of this user's unexpired slot."""
        slot = self._slot(user_id, token)
        try:
            report = json.loads((slot / REPORT).read_text())
        except (OSError, ValueError):
            raise StagedUploadNotFoundError(
                "준비된 업로드를 찾을 수 없습니다. 다시 올려 주세요."
            ) from None
        if report["expires_at"] < time.time():
            self.discard(slot)
            raise StagedUploadNotFoundError("준비된 업로드가 만료되었습니다. 다시 올려 주세요.")
        return report

    def claim(self, user_id: UUID, token: str, scan_id: UUID) -> dict:
        """Hand the prepared tree to one scan. The rename is atomic, so a token works once."""
        report = self.peek(user_id, token)
        target = self.root / CLAIMED / str(UUID(str(scan_id)))
        target.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.rename(self._slot(user_id, token), target)
        except OSError:
            raise StagedUploadNotFoundError(
                "준비된 업로드를 찾을 수 없습니다. 다시 올려 주세요."
            ) from None
        return report

    def claimed(self, scan_id: UUID) -> tuple[Path, dict] | None:
        """The exact tree that was inspected for this scan, if it came through staging."""
        slot = self.root / CLAIMED / str(UUID(str(scan_id)))
        try:
            return slot / TREE, json.loads((slot / REPORT).read_text())
        except (OSError, ValueError):
            return None

    def release(self, scan_id: UUID) -> None:
        shutil.rmtree(self.root / CLAIMED / str(UUID(str(scan_id))), ignore_errors=True)

    def purge_expired(self, now: float | None = None) -> int:
        """Delete expired or abandoned slots; safe to run at any time."""
        now = now or time.time()
        removed = 0
        users = self.root / "users"
        for user_root in list(users.iterdir()) if users.is_dir() else []:
            for slot in list(user_root.iterdir()):
                try:
                    expires_at = json.loads((slot / REPORT).read_text())["expires_at"]
                except (OSError, ValueError, KeyError):
                    # Unsealed: a request still in flight, or one that died mid-way.
                    expires_at = slot.stat().st_mtime + self.ttl_seconds
                if expires_at < now:
                    shutil.rmtree(slot, ignore_errors=True)
                    removed += 1
            if not any(user_root.iterdir()):
                user_root.rmdir()
        claimed = self.root / CLAIMED
        for slot in list(claimed.iterdir()) if claimed.is_dir() else []:
            if slot.stat().st_mtime + self.claimed_ttl_seconds < now:
                shutil.rmtree(slot, ignore_errors=True)
                removed += 1
        return removed
