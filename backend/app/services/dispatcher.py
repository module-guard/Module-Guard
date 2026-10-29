import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from uuid import UUID

from app.core.exceptions import ScanQueueFullError
from app.services.scan_service import ScanRunner, ScanService

logger = logging.getLogger(__name__)


class ScanDispatcher(ABC):
    """Replace submit with a Celery/RQ enqueue operation without changing the scan pipeline."""

    @abstractmethod
    def submit(self, scan_id: UUID) -> None: ...

    @abstractmethod
    def close(self) -> None: ...


class InProcessScanDispatcher(ScanDispatcher):
    def __init__(self, runner: ScanRunner, workers: int, queue_size: int):
        self.runner = runner
        self.slots = BoundedSemaphore(workers + queue_size)
        self.executor = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="scan")
        # Recover scans a previous process left mid-flight, without a hook in main.py. Only the
        # production single-worker deployment (one process per database) drives recovery on
        # startup: the demo dispatcher (a DemoScanService subclass), plain-function runners, and
        # the test harness, which shares one database across apps, all skip it. Each process
        # recovers by calling recover_interrupted directly. Failures must not block startup.
        owner = getattr(runner, "__self__", None)
        if type(owner) is ScanService and owner.settings.app_environment == "production":
            try:
                owner.recover_interrupted(self.submit)
            except Exception:
                logger.error("action=recovery_failed")

    def submit(self, scan_id: UUID, slot_reserved: bool = False) -> None:
        if not slot_reserved and not self.slots.acquire(blocking=False):
            raise ScanQueueFullError("Scan queue is full. Retry later.")
        try:
            future = self.executor.submit(self.runner, scan_id)
        except RuntimeError:
            self.slots.release()
            raise ScanQueueFullError("Scan executor is shutting down.") from None

        def done(completed):
            self.slots.release()
            if completed.exception() is not None:
                logger.error("scan_id=%s action=worker_failure", scan_id)

        future.add_done_callback(done)

    def reserve(self) -> None:
        """Take a queue slot before the scan exists, so a full queue is refused with no side effects."""
        if not self.slots.acquire(blocking=False):
            raise ScanQueueFullError("Scan queue is full. Retry later.")

    def cancel_reservation(self) -> None:
        """Return a slot taken by reserve() when the scan was not submitted after all."""
        self.slots.release()

    def close(self) -> None:
        self.executor.shutdown(wait=True)
