from fastapi import APIRouter, Query, Request, Response

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.dependencies import OptionalUser
from app.schemas.trust import TrustTimeline, VersionComparison
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
