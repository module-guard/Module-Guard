"""Read-only view of the scan transparency log. Nothing here writes, and a failing log never
changes scans, downloads or attestations."""

from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import JSONResponse

from app.api.dependencies import SessionDep
from app.auth.dependencies import OptionalUser
from app.auth.visibility import require_visible_scan
from app.core.exceptions import DomainError, NotFoundError
from app.schemas.transparency import (
    AnchorInfo,
    CheckpointInfo,
    CheckpointResponse,
    ConsistencyResponse,
    ScanTransparencyResponse,
)
from app.services.attestation_service import AttestationService
from app.transparency import log as tlog
from app.transparency import merkle

router = APIRouter(tags=["transparency"])


def _guard(request, user, name):
    caller = (
        f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    request.app.state.usage_service.preview_limiter.check(
        f"{name}:{caller}", "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."
    )


def _log(request):
    return tlog.TransparencyLog(request.app.state.scan_service.snapshot_store.root / "transparency")


def _unavailable(error: DomainError):
    # Same body as the app-wide handler, plus no-store like every response here.
    return JSONResponse(
        status_code=error.status_code,
        content={"error": {"code": error.code, "message": str(error)}},
        headers={"Cache-Control": "no-store"},
    )


@router.get("/transparency/checkpoint", response_model=CheckpointResponse)
def latest_checkpoint(request: Request, response: Response, user: OptionalUser):
    response.headers["Cache-Control"] = "no-store"
    _guard(request, user, "read-transparency")
    try:
        state = _log(request).read()
    except (tlog.LogUnavailable, tlog.LogInconsistent) as error:
        return _unavailable(error)
    head = state.tree_head
    if not state.exists:
        return CheckpointResponse()
    return CheckpointResponse(
        enabled=True,
        checkpoint=CheckpointInfo.model_validate(head) if head else None,
        anchors=[
            AnchorInfo.model_validate(
                {k: record.get(k) for k in ("type", "status", "receipt_sha256", "created_at")}
            )
            for (size, _), record in sorted(state.anchors.items())
            if head and size == head["tree_size"]
        ],
    )


@router.get("/transparency/consistency", response_model=ConsistencyResponse)
def consistency(
    request: Request,
    response: Response,
    user: OptionalUser,
    first: int = Query(ge=1, le=2**63),
    second: int = Query(ge=1, le=2**63),
):
    response.headers["Cache-Control"] = "no-store"
    _guard(request, user, "read-transparency")
    try:
        state = _log(request).read()
    except (tlog.LogUnavailable, tlog.LogInconsistent) as error:
        return _unavailable(error)
    heads = {c["tree_size"]: c for c in state.checkpoints}
    if first > second or first not in heads or second not in heads:
        raise NotFoundError("Checkpoint not found.")
    return ConsistencyResponse(
        first=first,
        second=second,
        first_root=heads[first]["root_hash"],
        second_root=heads[second]["root_hash"],
        proof=[p.hex() for p in merkle.consistency_proof(state.hashes, first, second)],
    )


@router.get(
    "/scans/{scan_id}/transparency",
    response_model=ScanTransparencyResponse,
    dependencies=[Depends(require_visible_scan)],
)
def scan_transparency(
    scan_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: OptionalUser,
):
    response.headers["Cache-Control"] = "no-store"
    _guard(request, user, "read-transparency")
    viewer = user.id if user else None
    # The same visibility rule as attestations: hidden, missing and mismatched are all 404.
    scan, version = AttestationService(
        session, request.app.state.scan_service.snapshot_store
    ).target(scan_id, version_id, viewer)
    problem = None
    try:
        state = _log(request).read()
    except tlog.LogInconsistent:
        state, problem = tlog.LogState(), ("mismatch", "LOG_INCONSISTENT")
    except tlog.LogUnavailable:
        state, problem = tlog.LogState(), ("not_logged", "LOG_UNAVAILABLE")
    result = tlog.describe_scan(session, scan, state)
    if problem:
        result.update(status=problem[0], reasons=[*result["reasons"], problem[1]])
    entry = result["entry"]
    if entry is not None and not tlog.shown_to(session, entry, version.id, viewer):
        result["entry"] = None
    return ScanTransparencyResponse(version_id=version.id, **result)
