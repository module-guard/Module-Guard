"""Recovery of scans a killed process left in a non-terminal state (Issue #10).

A crash is reproduced without killing anything: a scan is written straight to a stuck state in
the database, then a freshly built service runs its recovery pass, as it would at startup.
"""

import threading

import pytest

from app.models import Finding, Scan
from app.models.enums import ScanStatus, Severity
from app.repositories.scan_repository import RUNNING_STATES
from app.services.scan_service import ScanService


def build(sessions, settings, **overrides):
    return ScanService(sessions, settings.model_copy(update=overrides))


def seed(sessions, service, status, analysis=None, partial=False, warnings=None):
    """A scan parked in `status` with the given analysis, as a crash would leave it."""
    scan_id = service.create("https://github.com/org/repo").scan_id
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        scan.status = status
        scan.analysis = analysis
        scan.partial_result = partial
        scan.warnings = warnings or []
        session.commit()
    return scan_id


def load(sessions, scan_id):
    with sessions() as session:
        scan = session.get(Scan, scan_id)
        return scan.status, scan.analysis, scan.partial_result, scan.warnings


# ---------------------------------------------------------------- criterion 1: queued is requeued


def test_queued_scan_is_handed_back_to_the_dispatcher(sessions, settings):
    service = build(sessions, settings)
    scan_id = seed(sessions, service, ScanStatus.QUEUED)

    submitted = []
    service.recover_interrupted(submitted.append)

    # Re-registered, still QUEUED: the worker's own claim() flips it to COLLECTING when it runs.
    assert submitted == [scan_id]
    status, _, _, _ = load(sessions, scan_id)
    assert status == ScanStatus.QUEUED


def test_requeue_that_hits_a_full_queue_fails_cleanly(sessions, settings):
    service = build(sessions, settings)
    scan_id = seed(sessions, service, ScanStatus.QUEUED)

    def full(_scan_id):
        from app.core.exceptions import ScanQueueFullError

        raise ScanQueueFullError("full")

    service.recover_interrupted(full)

    status, analysis, _, _ = load(sessions, scan_id)
    assert status == ScanStatus.FAILED
    assert analysis["failure"] == {"stage": "collect", "code": "QueueUnavailable"}


# -------------------------------------------- criteria 2 & 8: running stages fail, state preserved


@pytest.mark.parametrize("status", RUNNING_STATES)
def test_running_scan_without_stages_fails_at_collect(sessions, settings, status):
    service = build(sessions, settings)
    scan_id = seed(sessions, service, status)

    service.recover_interrupted(lambda _scan_id: None)

    result_status, analysis, _, _ = load(sessions, scan_id)
    assert result_status == ScanStatus.FAILED
    assert analysis["failure"] == {"stage": "collect", "code": "Interrupted"}


def test_running_scan_preserves_stages_partial_and_findings(sessions, settings):
    service = build(sessions, settings)
    analysis = {
        "stages": {
            "checkov": {"status": "COMPLETED"},
            "parse": {"status": "RUNNING"},
            "graph": {"status": "PENDING"},
        },
        "entities": 7,
    }
    scan_id = seed(
        sessions,
        service,
        ScanStatus.SCANNING,
        analysis,
        partial=True,
        warnings=["checkov analysis is partial; inspect analysis diagnostics."],
    )
    with sessions() as session:
        session.add(
            Finding(scan_id=scan_id, rule_id="CKV_AWS_24", severity=Severity.HIGH, title="kept")
        )
        session.commit()

    service.recover_interrupted(lambda _scan_id: None)

    status, result, partial, warnings = load(sessions, scan_id)
    assert status == ScanStatus.FAILED
    # The furthest stage it had reached is the failed stage; later stages are skipped.
    assert result["failure"] == {"stage": "parse", "code": "Interrupted"}
    assert result["stages"]["checkov"]["status"] == "COMPLETED"
    assert result["stages"]["parse"]["status"] == "FAILED"
    assert result["stages"]["graph"]["status"] == "SKIPPED"
    assert result["entities"] == 7
    assert partial is True and warnings
    with sessions() as session:
        kept = session.scalars(select_findings(scan_id)).all()
    assert [f.rule_id for f in kept] == ["CKV_AWS_24"]


def select_findings(scan_id):
    from sqlalchemy import select

    return select(Finding).where(Finding.scan_id == scan_id)


# ------------------------------------------- criterion 3: a live worker's scan is not touched


def test_multiworker_leaves_a_fresh_scan_and_recovers_a_stale_one(sessions, settings):
    import time as time_module

    service = build(
        sessions,
        settings,
        scan_recovery_assume_single_worker=False,
        scan_recovery_stale_seconds=100,
    )
    now = time_module.time()
    fresh = seed(
        sessions,
        service,
        ScanStatus.SCANNING,
        {
            "stages": {"checkov": {"status": "RUNNING"}},
            "runtime": {"owner": "live", "heartbeat": now},
        },
    )
    stale = seed(
        sessions,
        service,
        ScanStatus.SCANNING,
        {
            "stages": {"checkov": {"status": "RUNNING"}},
            "runtime": {"owner": "dead", "heartbeat": now - 500},
        },
    )
    queued_fresh = seed(
        sessions,
        service,
        ScanStatus.QUEUED,
        {"runtime": {"owner": "live", "heartbeat": now}},
    )

    submitted = []
    service.recover_interrupted(submitted.append)

    assert load(sessions, fresh)[0] == ScanStatus.SCANNING  # untouched: its worker is alive
    assert load(sessions, stale)[0] == ScanStatus.FAILED  # abandoned: recovered
    assert queued_fresh not in submitted  # a fresh queued scan is left to its worker too


# ------------------------------------------- criterion 4: concurrent recovery acts once


def test_concurrent_recovery_fails_a_running_scan_exactly_once(sessions, settings):
    service = build(sessions, settings)
    scan_id = seed(
        sessions, service, ScanStatus.SCANNING, {"stages": {"checkov": {"status": "RUNNING"}}}
    )

    failed = []
    lock = threading.Lock()
    original = service.mark_failed

    def counting(sid, *args, **kwargs):
        with lock:
            failed.append(sid)
        return original(sid, *args, **kwargs)

    service.mark_failed = counting
    threads = [
        threading.Thread(target=service.recover_interrupted, args=(lambda _s: None,))
        for _ in range(2)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    # The atomic status flip lets only one pass write the failure.
    assert failed == [scan_id]
    assert load(sessions, scan_id)[0] == ScanStatus.FAILED


# ------------------------------------------- startup wiring: production recovers, others do not


def test_production_dispatcher_recovers_on_construction(sessions, settings):
    from app.services.dispatcher import InProcessScanDispatcher

    service = build(sessions, settings, app_environment="production")
    scan_id = seed(
        sessions, service, ScanStatus.SCANNING, {"stages": {"checkov": {"status": "RUNNING"}}}
    )

    dispatcher = InProcessScanDispatcher(service.run, workers=1, queue_size=2)
    try:
        # Constructing the dispatcher runs recovery synchronously in a production deployment.
        status, analysis, _, _ = load(sessions, scan_id)
        assert status == ScanStatus.FAILED
        assert analysis["failure"]["code"] == "Interrupted"
    finally:
        dispatcher.close()


def test_non_production_dispatcher_leaves_scans_untouched(sessions, settings):
    from app.services.dispatcher import InProcessScanDispatcher

    service = build(sessions, settings)  # development: the shared-database test harness
    scan_id = seed(
        sessions, service, ScanStatus.SCANNING, {"stages": {"checkov": {"status": "RUNNING"}}}
    )

    dispatcher = InProcessScanDispatcher(service.run, workers=1, queue_size=2)
    try:
        assert load(sessions, scan_id)[0] == ScanStatus.SCANNING
    finally:
        dispatcher.close()
