from fastapi import APIRouter, Query, Request, Response
from sqlalchemy import select

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.dependencies import OptionalUser
from app.core.exceptions import NotFoundError
from app.models import ReuseRecord
from app.schemas.trust import ReuseEvidence, ReuseHistory, TrustTimeline, VersionComparison
from app.services.attestation_service import AttestationService, load_policy
from app.services.trust_service import compare_versions, timeline, visible_version

router = APIRouter(prefix="/repositories/{owner}/{name}/trust", tags=["trust"])


@router.get("/compare", response_model=VersionComparison)
def compare(
    owner: str,
    name: str,
    before: str,
    after: str,
    session: SessionDep,
    response: Response,
    user: OptionalUser,
    limit: Limit = 50,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    viewer = user.id if user else None
    a = visible_version(session, owner, name, before, viewer)
    b = visible_version(session, owner, name, after, viewer)
    return compare_versions(session, a, b, limit, offset)


@router.get("/timeline", response_model=TrustTimeline)
def events(
    owner: str,
    name: str,
    session: SessionDep,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Query(None, max_length=100),
):
    response.headers["Cache-Control"] = "no-store"
    target = visible_version(session, owner, name, version, user.id if user else None)
    return timeline(session, target, request.app.state.scan_service.snapshot_store)


@router.get("/reuse", response_model=ReuseHistory)
def reuse_history(
    owner: str,
    name: str,
    session: SessionDep,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    target = visible_version(session, owner, name, version, user.id if user else None)
    rows = session.scalars(
        select(ReuseRecord)
        .where(ReuseRecord.target_scan_id == target.scan_id)
        .order_by(ReuseRecord.created_at, ReuseRecord.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    result = []
    service = AttestationService(
        session, request.app.state.scan_service.snapshot_store, load_policy()
    )
    for row in rows[:limit]:
        evidence = ReuseEvidence.model_validate(row, from_attributes=True)
        if row.source_version_id and row.source_scan_id:
            try:
                source, v = service.target(
                    row.source_scan_id, row.source_version_id, user.id if user else None
                )
                evidence.current_source_status = service.describe(source, v).status
            except NotFoundError:
                evidence.source_scan_id = None
                evidence.source_finished_at = None
                evidence.source_duration_ms = None
                evidence.key = None
                evidence.current_source_status = "unavailable"
        result.append(evidence)
    return ReuseHistory(records=result, next_offset=offset + limit if len(rows) > limit else None)
