from threading import Event
from uuid import uuid4

import pytest

from app.core.exceptions import ScanQueueFullError
from app.services.dispatcher import InProcessScanDispatcher


def test_bounded_dispatcher():
    entered = Event()
    release = Event()

    def runner(scan_id):
        entered.set()
        assert release.wait(5)

    dispatcher = InProcessScanDispatcher(runner, workers=1, queue_size=0)
    try:
        dispatcher.submit(uuid4())
        assert entered.wait(5)
        with pytest.raises(ScanQueueFullError):
            dispatcher.submit(uuid4())
    finally:
        release.set()
        dispatcher.close()
