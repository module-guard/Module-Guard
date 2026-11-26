from datetime import UTC, datetime
from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import and_, func, literal, or_, select

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.dependencies import AdminUser, OptionalUser, verify_origin
from app.auth.visibility import repository_visible_to
from app.core.exceptions import NotFoundError
from app.models import AllowBlockEntry, AllowBlockEvent, Repository, User
from app.schemas.allowblock import (
    AdminEntry,
    AdminEntryPage,
    AdminEvent,
    EntryCreate,
    EntryRevoke,
    EventPage,
    PublicEntry,
    PublicEntryPage,
    RepositoryPolicy,
)
from app.schemas.social import RepositoryRef
from app.services import allowblock_service as policy

router = APIRouter(tags=["allowblock"])
ADMIN = "/admin/allowblock/entries"
MESSAGES = {
    "BLOCK": "정책에 따라 이 소스는 등록하거나 수집할 수 없습니다.",
    "ALLOW": "이 소스는 허용 목록에 있습니다. 기존 검사는 그대로 적용됩니다.",
}
LIMITED = "관리 요청이 너무 많습니다. 잠시 후 다시 시도해 주세요."


def _admin_entry(entry: AllowBlockEntry) -> AdminEntry:
    return AdminEntry(
        id=entry.id,
        target_kind=entry.target_kind,
        target_key=entry.target_key,
        repository_id=entry.repository_id,
        effect=entry.effect,
        status=policy.entry_status(entry),
        reason=entry.reason,
        evidence_urls=entry.evidence_urls,
        public_message=entry.public_message,
        created_by_login=entry.created_by_login,
        created_at=entry.created_at,
        expires_at=entry.expires_at,
        revoked_at=entry.revoked_at,
        revoked_by_login=entry.revoked_by_login,
        revoke_reason=entry.revoke_reason,
        revoke_evidence_urls=entry.revoke_evidence_urls,
    )


def _entry(session, entry_id: UUID) -> AllowBlockEntry:
    entry = session.get(AllowBlockEntry, entry_id)
    if entry is None:
        raise NotFoundError("Entry not found.")
    return entry


# ------------------------------------------------------------------ admin


@router.post(
    ADMIN, response_model=AdminEntry, status_code=201, dependencies=[Depends(verify_origin)]
)
def create_entry(payload: EntryCreate, request: Request, session: SessionDep, admin: AdminUser):
    request.app.state.allowblock_limiter.check(admin.id, LIMITED)
    entry = policy.create_entry(session, admin, **payload.model_dump())
    return _admin_entry(entry)


@router.get(ADMIN, response_model=AdminEntryPage)
def list_entries(
    response: Response,
    session: SessionDep,
    admin: AdminUser,
    status: Literal["active", "inactive", "all"] = "active",
    effect: Literal["ALLOW", "BLOCK"] | None = None,
    target_kind: Literal["repository", "github_repository", "github_owner"] | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    query = select(AllowBlockEntry)
    live = policy.active(datetime.now(UTC))
    if status == "active":
        query = query.where(live)
    elif status == "inactive":
        query = query.where(~live)
    if effect:
        query = query.where(AllowBlockEntry.effect == effect)
    if target_kind:
        query = query.where(AllowBlockEntry.target_kind == target_kind)
    rows = session.scalars(
        query.order_by(AllowBlockEntry.created_at.desc(), AllowBlockEntry.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return AdminEntryPage(
        entries=[_admin_entry(r) for r in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


@router.get(ADMIN + "/{entry_id}", response_model=AdminEntry)
def get_entry(entry_id: UUID, response: Response, session: SessionDep, admin: AdminUser):
    response.headers["Cache-Control"] = "no-store"
    return _admin_entry(_entry(session, entry_id))


@router.post(
    ADMIN + "/{entry_id}/revoke", response_model=AdminEntry, dependencies=[Depends(verify_origin)]
)
def revoke_entry(
    entry_id: UUID,
    payload: EntryRevoke,
    request: Request,
    session: SessionDep,
    admin: AdminUser,
):
    request.app.state.allowblock_limiter.check(admin.id, LIMITED)
    return _admin_entry(policy.revoke_entry(session, admin, entry_id, **payload.model_dump()))


@router.get(ADMIN + "/{entry_id}/events", response_model=EventPage)
def entry_history(
    entry_id: UUID,
    response: Response,
    session: SessionDep,
    admin: AdminUser,
    limit: Limit = 50,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "no-store"
    _entry(session, entry_id)
    rows = session.scalars(
        select(AllowBlockEvent)
        .where(AllowBlockEvent.entry_id == entry_id)
        .order_by(AllowBlockEvent.occurred_at, AllowBlockEvent.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return EventPage(
        events=[AdminEvent.model_validate(r, from_attributes=True) for r in rows[:limit]],
        next_offset=offset + limit if len(rows) > limit else None,
    )


# ------------------------------------------------------------------ public


def _applies_to_repository():
    """SQL join condition: the entry names this repository, its GitHub origin or its owner."""
    origin = func.lower(Repository.origin_url)
    return or_(
        and_(
            AllowBlockEntry.target_kind == "repository",
            AllowBlockEntry.repository_id == Repository.id,
        ),
        and_(
            AllowBlockEntry.target_kind == "github_repository",
            origin == AllowBlockEntry.target_key,
        ),
        and_(
            AllowBlockEntry.target_kind == "github_owner",
            origin.like(literal("https://github.com/") + AllowBlockEntry.target_key + "/%"),
        ),
    )


def _public(entry: AllowBlockEntry, repository: RepositoryRef | None = None) -> PublicEntry:
    return PublicEntry(
        repository=repository,
        effect=entry.effect,
        message=entry.public_message or MESSAGES[entry.effect],
        expires_at=entry.expires_at,
    )


@router.get("/allowblock/entries", response_model=PublicEntryPage)
def public_entries(
    response: Response,
    session: SessionDep,
    user: OptionalUser,
    effect: Literal["ALLOW", "BLOCK"] | None = None,
    limit: Limit = 20,
    offset: Offset = 0,
):
    response.headers["Cache-Control"] = "private, no-store"
    query = (
        select(AllowBlockEntry, Repository.name, User.login)
        .join(Repository, _applies_to_repository())
        .join(User, User.id == Repository.owner_id)
        .where(policy.active(datetime.now(UTC)), repository_visible_to(user.id if user else None))
    )
    if effect:
        query = query.where(AllowBlockEntry.effect == effect)
    # Visibility is part of the query, so a page never contains a hidden repository.
    rows = session.execute(
        query.order_by(AllowBlockEntry.created_at.desc(), AllowBlockEntry.id, Repository.id)
        .offset(offset)
        .limit(limit + 1)
    ).all()
    return PublicEntryPage(
        entries=[
            _public(e, RepositoryRef(owner=login, name=name)) for e, name, login in rows[:limit]
        ],
        next_offset=offset + limit if len(rows) > limit else None,
    )


@router.get("/repositories/{owner}/{name}/policy", response_model=RepositoryPolicy)
def repository_policy(
    owner: str, name: str, response: Response, session: SessionDep, user: OptionalUser
):
    response.headers["Cache-Control"] = "private, no-store"
    repository = session.scalar(
        select(Repository)
        .join(User, User.id == Repository.owner_id)
        .where(
            func.lower(User.login) == owner.lower(),
            Repository.name == name,
            repository_visible_to(user.id if user else None),
        )
    )
    if repository is None:
        raise NotFoundError("Repository not found.")
    entries = session.scalars(
        select(AllowBlockEntry)
        .join(Repository, _applies_to_repository())
        .where(policy.active(datetime.now(UTC)), Repository.id == repository.id)
        .order_by(AllowBlockEntry.created_at, AllowBlockEntry.id)
    ).all()
    effects = {e.effect for e in entries}
    return RepositoryPolicy(
        repository=RepositoryRef(owner=repository.owner.login, name=repository.name),
        effect="BLOCK" if "BLOCK" in effects else "ALLOW" if "ALLOW" in effects else "NONE",
        entries=[_public(e) for e in entries],
    )
