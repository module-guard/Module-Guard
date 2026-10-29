"""Owner-initiated retry of a failed scan (Issue #10).

A failed publish is produced by registering a repository and marking its first scan FAILED
before it runs. The dispatcher is replaced by a manual stand-in so a registered scan stays put
until the test runs it, and so a full queue can be simulated deterministically.
"""

import threading
from uuid import UUID

import pytest
from conftest import prepare, register, sign_in
from sqlalchemy import func, select

from app.core.exceptions import DomainError, ScanQueueFullError, UploadRejectedError
from app.models import Finding, ModuleVersion, Scan, User
from app.models.enums import ScanStatus, Severity


class ManualDispatcher:
    """Records submitted scans instead of running them; `full` simulates a saturated queue."""

    def __init__(self):
        self.submitted = []
        self.full = False

    def reserve(self):
        if self.full:
            raise ScanQueueFullError("full")

    def cancel_reservation(self):
        pass

    def submit(self, scan_id, slot_reserved=False):
        if self.full and not slot_reserved:
            raise ScanQueueFullError("full")
        self.submitted.append(scan_id)

    def close(self):
        pass


def make_failed(client, sessions, name="demo", upload_id=None):
    """Register a repository and leave its first scan FAILED, returning the scan id (str)."""
    upload_id = upload_id or prepare(client)["upload_id"]
    response = register(client, upload_id, name)
    assert response.status_code == 201, response.text
    scan_id = response.json()["scan_id"]
    with sessions() as session:
        scan = session.get(Scan, UUID(scan_id))
        scan.status = ScanStatus.FAILED
        scan.error_message = "이전 시도 실패"
        session.commit()
    return scan_id


def make_failed_github(client, sessions, name="ghrepo", url="https://github.com/org/demo"):
    """Register a GitHub-sourced repository and leave its first scan FAILED."""
    report = client.post("/api/uploads/github", json={"url": url}).json()
    response = register(client, report["upload_id"], name)
    assert response.status_code == 201, response.text
    scan_id = response.json()["scan_id"]
    with sessions() as session:
        session.get(Scan, UUID(scan_id)).status = ScanStatus.FAILED
        session.commit()
    return scan_id


def retry(client, scan_id, upload_id, **headers):
    return client.post(
        f"/api/scans/{scan_id}/retry", json={"upload_id": upload_id}, headers=headers or None
    )


def retry_attempts(sessions, old_id):
    with sessions() as session:
        scans = session.scalars(select(Scan)).all()
    return [scan.id for scan in scans if (scan.analysis or {}).get("retry_of") == old_id]


# ---------------------------------------------------------------- happy path: reuse, no duplicates


def test_retry_reuses_the_version_links_the_old_scan_and_completes(stack, sessions):
    disp = ManualDispatcher()
    client = stack(dispatcher=disp)
    sign_in(client, sessions)
    old_id = make_failed(client, sessions)
    # A partial finding on the old attempt must survive the retry.
    with sessions() as session:
        session.add(
            Finding(scan_id=UUID(old_id), rule_id="CKV_OLD", severity=Severity.HIGH, title="old")
        )
        session.commit()

    response = retry(client, old_id, prepare(client)["upload_id"])
    assert response.status_code == 202, response.text
    new_id = response.json()["scan_id"]
    assert new_id != old_id
    assert UUID(new_id) in disp.submitted
    assert response.headers["location"] == f"/api/scans/{new_id}"

    client.scans.run(UUID(new_id))
    new_scan = client.get(f"/api/scans/{new_id}").json()
    assert new_scan["status"] == "COMPLETED"
    assert new_scan["retry_of"] == old_id

    old_scan = client.get(f"/api/scans/{old_id}").json()
    assert old_scan["status"] == "FAILED"
    assert old_scan["retried_by"] == new_id

    with sessions() as session:
        versions = session.scalar(select(func.count(ModuleVersion.id)))
        version = session.scalar(select(ModuleVersion))
        old_findings = session.scalar(
            select(func.count(Finding.id)).where(Finding.scan_id == UUID(old_id))
        )
        new_findings = session.scalar(
            select(func.count(Finding.id)).where(Finding.scan_id == UUID(new_id))
        )
    assert versions == 1  # the version was reused, not duplicated
    assert version.scan_id == UUID(new_id) and version.snapshot_sha256 is not None
    assert old_findings == 1  # the old attempt's finding is preserved
    assert new_findings > 0  # the new attempt has its own findings


# ---------------------------------------------------------------- authorisation


def test_retry_refused_for_another_user(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    sign_in(client, sessions, login="alice", github_id=1)
    old_id = make_failed(client, sessions)

    sign_in(client, sessions, login="bob", github_id=2)
    assert retry(client, old_id, prepare(client)["upload_id"]).status_code == 404


def test_retry_refused_for_foreign_origin(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    sign_in(client, sessions)
    old_id = make_failed(client, sessions)

    response = retry(client, old_id, prepare(client)["upload_id"], Origin="http://evil.example")
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "ForbiddenOriginError"


# ---------------------------------------------------------------- prepared source gone


def test_retry_with_consumed_source_asks_to_upload_again(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    sign_in(client, sessions)
    # The first scan claims this upload, so the prepared source is already gone.
    upload = prepare(client)["upload_id"]
    old_id = make_failed(client, sessions, upload_id=upload)

    response = retry(client, old_id, upload)
    assert response.status_code == 404
    assert "다시 올려" in response.json()["error"]["message"]


# ---------------------------------------------------------------- full queue


def test_retry_refused_when_queue_full_without_creating_a_scan(stack, sessions):
    disp = ManualDispatcher()
    client = stack(dispatcher=disp)
    sign_in(client, sessions)
    old_id = make_failed(client, sessions)
    upload = prepare(client)["upload_id"]

    disp.full = True
    response = retry(client, old_id, upload)
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "ScanQueueFullError"

    # Nothing new was created and the old scan is untouched (still retryable).
    with sessions() as session:
        scans = session.scalar(select(func.count(Scan.id)))
        old = session.get(Scan, UUID(old_id))
    assert scans == 1
    assert old.status == ScanStatus.FAILED and (old.analysis or {}).get("retried_by") is None


# ---------------------------------------------------------------- idempotency


def test_repeated_retry_creates_a_single_attempt(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    sign_in(client, sessions)
    old_id = make_failed(client, sessions)

    first = retry(client, old_id, prepare(client)["upload_id"])
    assert first.status_code == 202
    second = retry(client, old_id, prepare(client)["upload_id"])
    assert second.status_code == 409

    assert retry_attempts(sessions, old_id) == [UUID(first.json()["scan_id"])]


def test_concurrent_retry_creates_a_single_attempt(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    alice_id = sign_in(client, sessions)
    old_id = make_failed(client, sessions)
    upload = prepare(client)["upload_id"]
    with sessions() as session:
        alice = session.get(User, alice_id)

    results, errors = [], []
    lock = threading.Lock()

    def attempt():
        try:
            accepted = client.scans.retry(alice, UUID(old_id), upload, False)
            with lock:
                results.append(accepted.scan_id)
        except DomainError as error:
            with lock:
                errors.append(type(error).__name__)

    threads = [threading.Thread(target=attempt) for _ in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(results) == 1 and len(errors) == 1  # exactly one attempt won
    assert retry_attempts(sessions, old_id) == results


# ---------------------------------------------------------------- recovery frees the running limit


def test_recovery_restores_the_running_limit(stack, sessions):
    disp = ManualDispatcher()
    client = stack(dispatcher=disp, max_running_publishes=1)
    sign_in(client, sessions)

    first = register(client, prepare(client)["upload_id"], "demo").json()["scan_id"]
    with sessions() as session:
        session.get(Scan, UUID(first)).status = ScanStatus.SCANNING
        session.commit()

    # The single slot is taken by the stuck scan, so a new publish is rate limited.
    assert register(client, prepare(client)["upload_id"], "other").status_code == 429

    client.scans.recover_interrupted(disp.submit)
    with sessions() as session:
        assert session.get(Scan, UUID(first)).status == ScanStatus.FAILED

    # With the stuck scan resolved, publishing is allowed again.
    assert register(client, prepare(client)["upload_id"], "other").status_code == 201


# ---------------------------------------------------------------- source must match the version


def test_retry_rejects_a_mismatched_source_type(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    alice_id = sign_in(client, sessions)
    old_id = make_failed_github(client, sessions)
    zip_upload = prepare(client)["upload_id"]  # an UPLOAD source for a GitHub version
    with sessions() as session:
        alice = session.get(User, alice_id)

    with pytest.raises(UploadRejectedError) as raised:
        client.scans.retry(alice, UUID(old_id), zip_upload, False)
    assert raised.value.code == "SourceMismatch"
    assert retry_attempts(sessions, old_id) == []


def test_retry_api_refuses_a_different_source_type_and_changes_nothing(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    alice_id = sign_in(client, sessions)
    old_id = make_failed_github(client, sessions)
    zip_upload = prepare(client)["upload_id"]

    response = retry(client, old_id, zip_upload)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SourceMismatch"

    # No new scan, the version is still unlinked, and the prepared upload is untouched.
    with sessions() as session:
        scans = session.scalar(select(func.count(Scan.id)))
        version = session.scalar(select(ModuleVersion))
        old = session.get(Scan, UUID(old_id))
    assert scans == 1
    assert version.scan_id is None
    assert (old.analysis or {}).get("retried_by") is None
    assert client.publish.staging.peek(alice_id, zip_upload)["origin_type"] == "UPLOAD"


def test_retry_api_refuses_a_different_github_url(stack, sessions):
    client = stack(dispatcher=ManualDispatcher())
    sign_in(client, sessions)
    old_id = make_failed_github(client, sessions, url="https://github.com/org/demo")

    other = client.post("/api/uploads/github", json={"url": "https://github.com/org/other"}).json()
    response = retry(client, old_id, other["upload_id"])
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "SourceMismatch"
    assert retry_attempts(sessions, old_id) == []
