from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from starlette.concurrency import run_in_threadpool

from app.api.dependencies import SessionDep
from app.auth.dependencies import CurrentUser, verify_origin
from app.auth.visibility import require_visible_scan
from app.core.exceptions import NotFoundError, ScanQueueFullError
from app.repositories.scan_repository import ScanRepository
from app.schemas.scan import ScanAccepted, ScanRequest, ScanResponse, ScanRetryRequest

router = APIRouter(prefix="/scans", tags=["scans"], dependencies=[Depends(require_visible_scan)])


@router.post("", status_code=202, response_model=ScanAccepted)
async def create_scan(payload: ScanRequest, request: Request, response: Response):
    """Anonymous direct scans bypass the publish checks, so they exist only for development."""
    if request.app.state.settings.app_environment != "development":
        raise NotFoundError("Not found.")
    service = request.app.state.scan_service
    accepted = await run_in_threadpool(service.create, payload.source)
    try:
        request.app.state.dispatcher.submit(accepted.scan_id)
    except ScanQueueFullError:
        await run_in_threadpool(
            service.mark_failed, accepted.scan_id, "Scan queue is unavailable. Submit a new scan."
        )
        raise
    response.headers["Location"] = f"/api/scans/{accepted.scan_id}"
    return accepted


@router.get("/{scan_id}", response_model=ScanResponse)
def get_scan(scan_id: UUID, session: SessionDep):
    return ScanResponse.from_scan(ScanRepository(session).get(scan_id))


@router.post(
    "/{scan_id}/retry",
    status_code=202,
    response_model=ScanAccepted,
    dependencies=[Depends(verify_origin)],
)
async def retry_scan(
    scan_id: UUID,
    payload: ScanRetryRequest,
    request: Request,
    response: Response,
    user: CurrentUser,
):
    """Retry the caller's failed scan from a freshly prepared source. The owner only."""
    service = request.app.state.scan_service
    dispatcher = request.app.state.dispatcher
    # Refuse a full queue before any new scan is created, so a rejection leaves no stray record.
    dispatcher.reserve()
    try:
        accepted = await run_in_threadpool(
            service.retry, user, scan_id, payload.upload_id, payload.acknowledge_warnings
        )
    except BaseException:
        dispatcher.cancel_reservation()
        raise
    dispatcher.submit(accepted.scan_id, slot_reserved=True)
    response.headers["Location"] = f"/api/scans/{accepted.scan_id}"
    return accepted
