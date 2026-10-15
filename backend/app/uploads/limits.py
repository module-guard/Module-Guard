"""Per-user limits for the expensive, synchronous "prepare" step (single-process)."""

import threading
import time
from collections import defaultdict, deque
from contextlib import contextmanager
from uuid import UUID

from app.core.exceptions import RateLimitedError


class PreflightLimiter:
    def __init__(self, per_hour: int, clock=time.monotonic):
        self.per_hour = per_hour
        self.clock = clock
        self.lock = threading.Lock()
        self.active: set[UUID] = set()
        self.history: dict[UUID, deque[float]] = defaultdict(deque)

    @contextmanager
    def slot(self, user_id: UUID):
        """One preparation at a time per user, and a bounded number per hour."""
        now = self.clock()
        with self.lock:
            recent = self.history[user_id]
            while recent and recent[0] <= now - 3600:
                recent.popleft()
            if user_id in self.active:
                raise RateLimitedError(
                    "이미 처리 중인 업로드가 있습니다. 끝난 뒤 다시 시도해 주세요."
                )
            if len(recent) >= self.per_hour:
                raise RateLimitedError("업로드 시도가 너무 많습니다. 잠시 뒤 다시 시도해 주세요.")
            recent.append(now)
            self.active.add(user_id)
        try:
            yield
        finally:
            with self.lock:
                self.active.discard(user_id)


class HourlyLimiter:
    """A bounded number of actions per caller per hour (single-process, like PreflightLimiter).

    The key is a user id, or any string that stands for the caller (an address when signed out).
    """

    def __init__(self, per_hour: int, clock=time.monotonic):
        self.per_hour = per_hour
        self.clock = clock
        self.lock = threading.Lock()
        self.history: dict[UUID | str, deque[float]] = defaultdict(deque)

    def check(self, user_id: UUID | str, message: str) -> None:
        now = self.clock()
        with self.lock:
            recent = self.history[user_id]
            while recent and recent[0] <= now - 3600:
                recent.popleft()
            if len(recent) >= self.per_hour:
                raise RateLimitedError(message)
            recent.append(now)
