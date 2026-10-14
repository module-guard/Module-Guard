import logging
from abc import ABC, abstractmethod
from concurrent.futures import ThreadPoolExecutor
from threading import BoundedSemaphore
from uuid import UUID

from app.core.exceptions import ScanQueueFullError
from app.services.scan_service import ScanRunner

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

    def submit(self, scan_id: UUID) -> None:
        if not self.slots.acquire(blocking=False):
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

    def close(self) -> None:
        self.executor.shutdown(wait=True)
