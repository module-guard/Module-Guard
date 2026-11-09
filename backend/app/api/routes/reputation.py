from datetime import UTC, datetime, timedelta

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.dependencies import CurrentUser, OptionalUser, verify_origin
from app.core.exceptions import NotFoundError
from app.models import Repository
from app.models.reputation import ReputationSnapshot
from app.schemas.reputation import ReputationEvaluation, ReputationHistory
from app.services.reputation_service import collect
from app.services.trust_service import visible_version

router = APIRouter(prefix="/repositories/{owner}/{name}/reputation", tags=["reputation"])


@router.get("", response_model=ReputationHistory)
def history(
    owner: str,
    name: str,
    response: Response,
    session: SessionDep,
    user: OptionalUser,
    version: str | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    target = visible_version(session, owner, name, version, user.id if user else None)
    rows = session.scalars(
        select(ReputationSnapshot)
        .where(ReputationSnapshot.version_id == target.id)
        .order_by(ReputationSnapshot.collected_at.desc(), ReputationSnapshot.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    entries = []
    for row in rows[:limit]:
        entry = ReputationEvaluation.model_validate(row.payload)
        if entry.collected_at < datetime.now(UTC) - timedelta(days=7):
            for metric in entry.metrics:
                if metric.status == "confirmed":
                    metric.status = "stale"
        entries.append(entry)
    return ReputationHistory(
        entries=entries, next_offset=offset + limit if len(rows) > limit else None
    )


@router.post("/collect", response_model=ReputationEvaluation, dependencies=[Depends(verify_origin)])
def refresh(
    owner: str,
    name: str,
    response: Response,
    request: Request,
    session: SessionDep,
    user: CurrentUser,
    version: str | None = None,
):
    response.headers["Cache-Control"] = "no-store"
    target = visible_version(session, owner, name, version, user.id)
    if session.get(Repository, target.repository_id).owner_id != user.id:
        raise NotFoundError("Version not found.")
    request.app.state.usage_service.preview_limiter.check(
        f"reputation:{user.id}", "수집 요청이 많습니다. 잠시 후 다시 시도해 주세요."
    )
    return collect(session, target)
