"""Allow/block list (#47): the one matcher every admission point calls.

Nothing outside this module decides what a source or a repository may do. An ALLOW entry never
skips another check, and an active BLOCK always wins. Entries are never deleted: a revoked or
expired entry stays as history, and every change appends an `allowblock_events` row in the same
transaction.
"""

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.collectors.source_detector import normalize_github_url
from app.core.exceptions import (
    ConflictError,
    InvalidPolicyEntryError,
    NotFoundError,
    SourceBlockedError,
    UnsupportedSourceError,
)
from app.models import AllowBlockEntry, AllowBlockEvent, Repository, User
from app.models.allowblock import EFFECTS, TARGET_KINDS

OWNER = re.compile(r"[a-z0-9][a-z0-9-]{0,38}")
MAX_EVIDENCE = 10


@dataclass(frozen=True)
class Decision:
    """`effect` is BLOCK, ALLOW or None (no active entry)."""

    effect: str | None
    entry_id: UUID | None = None


def _utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        raise InvalidPolicyEntryError("Timestamps must include a timezone.")
    return value.astimezone(UTC)


def active(now: datetime):
    """SQL condition: not revoked, and not expired (exclusive at the boundary)."""
    return and_(
        AllowBlockEntry.revoked_at.is_(None),
        or_(AllowBlockEntry.expires_at.is_(None), AllowBlockEntry.expires_at > now),
    )


def source_keys(source: str) -> list[tuple[str, str]]:
    """The targets a source falls under; empty for anything that is not a GitHub URL."""
    try:
        normalized = normalize_github_url(source)
    except UnsupportedSourceError:
        return []
    owner = normalized.removeprefix("https://github.com/").split("/")[0]
    return [("github_repository", normalized), ("github_owner", owner)]


def decide(session: Session, keys: list[tuple[str, str]], now: datetime | None = None) -> Decision:
    if not keys:
        return Decision(None)
    now = _utc(now or datetime.now(UTC))
    rows = session.execute(
        select(AllowBlockEntry.id, AllowBlockEntry.effect)
        .where(
            active(now),
            or_(
                *[
                    and_(AllowBlockEntry.target_kind == k, AllowBlockEntry.target_key == v)
                    for k, v in keys
                ]
            ),
        )
        .order_by(AllowBlockEntry.created_at, AllowBlockEntry.id)
    ).all()
    for effect in ("BLOCK", "ALLOW"):
        for entry_id, found in rows:
            if found == effect:
                return Decision(effect, entry_id)
    return Decision(None)


def enforce_source(session: Session, source: str | None, now: datetime | None = None) -> None:
    if source and decide(session, source_keys(source), now).effect == "BLOCK":
        raise SourceBlockedError()


def enforce_repository(session: Session, repository_id: UUID, now: datetime | None = None) -> None:
    keys = [("repository", str(repository_id))]
    if decide(session, keys, now).effect == "BLOCK":
        raise SourceBlockedError()


class SourceGuard:
    """What collectors and batch jobs hold: a check that opens its own short session."""

    def __init__(self, session_factory: sessionmaker[Session]):
        self.session_factory = session_factory

    def check(self, source: str | None, repository_id: UUID | None = None) -> None:
        with self.session_factory() as session:
            enforce_source(session, source)
            if repository_id is not None:
                enforce_repository(session, repository_id)

    __call__ = check


# ------------------------------------------------------------------ lifecycle


def normalize_target(session: Session, kind: str, target: str) -> tuple[str, UUID | None]:
    """The stored key of a target, and the platform repository it names (if it is one)."""
    if kind not in TARGET_KINDS:
        raise InvalidPolicyEntryError("Unknown target kind.")
    target = target.strip()
    if kind == "repository":
        try:
            repository_id = UUID(target)
        except ValueError:
            raise InvalidPolicyEntryError("A repository target is a repository id.") from None
        if session.get(Repository, repository_id) is None:
            raise NotFoundError("Repository not found.")
        return str(repository_id), repository_id
    if kind == "github_repository":
        return normalize_github_url(target), None
    owner = target.lower()
    if not OWNER.fullmatch(owner):
        raise InvalidPolicyEntryError("Use a GitHub owner name such as hashicorp.")
    return owner, None


def _snapshot(entry: AllowBlockEntry) -> dict:
    def when(value):
        return value.isoformat() if value else None

    return {
        "id": str(entry.id),
        "target_kind": entry.target_kind,
        "target_key": entry.target_key,
        "repository_id": str(entry.repository_id) if entry.repository_id else None,
        "effect": entry.effect,
        "reason": entry.reason,
        "evidence_urls": list(entry.evidence_urls),
        "public_message": entry.public_message,
        "created_by_login": entry.created_by_login,
        "created_at": when(entry.created_at),
        "expires_at": when(entry.expires_at),
        "revoked_at": when(entry.revoked_at),
    }


def _event(session, entry, action, actor, reason, evidence, now) -> None:
    session.add(
        AllowBlockEvent(
            entry_id=entry.id,
            action=action,
            actor_id=actor.id if actor else None,
            actor_login=actor.login if actor else "system",
            occurred_at=now,
            reason=reason,
            evidence_urls=list(evidence),
            snapshot=_snapshot(entry),
        )
    )


def _evidence(urls: list[str]) -> list[str]:
    if len(urls) > MAX_EVIDENCE or any(not u.startswith(("https://", "http://")) for u in urls):
        raise InvalidPolicyEntryError("Evidence must be at most 10 http(s) URLs.")
    return list(urls)


def create_entry(
    session: Session,
    admin: User,
    *,
    target_kind: str,
    target: str,
    effect: str,
    reason: str,
    evidence_urls: list[str],
    public_message: str | None = None,
    expires_at: datetime | None = None,
    now: datetime | None = None,
) -> AllowBlockEntry:
    """Register an entry and its CREATE event in one transaction.

    A same-target entry that has already expired but was never closed is closed here (with an
    EXPIRE event) so the unique slot is free; a still-active one is a conflict.
    """
    now = _utc(now or datetime.now(UTC))
    if effect not in EFFECTS:
        raise InvalidPolicyEntryError("Effect must be ALLOW or BLOCK.")
    if not reason.strip():
        raise InvalidPolicyEntryError("A reason is required.")
    evidence = _evidence(evidence_urls)
    if expires_at is not None:
        expires_at = _utc(expires_at)
        if expires_at <= now:
            raise InvalidPolicyEntryError("The expiry must be in the future.")
    key, repository_id = normalize_target(session, target_kind, target)
    try:
        stale = session.scalar(
            select(AllowBlockEntry).where(
                AllowBlockEntry.target_kind == target_kind,
                AllowBlockEntry.target_key == key,
                AllowBlockEntry.effect == effect,
                AllowBlockEntry.revoked_at.is_(None),
            )
        )
        if stale is not None:
            if stale.expires_at is None or _utc_db(stale.expires_at) > now:
                raise ConflictError("An active entry for this target already exists.")
            stale.revoked_at = _utc_db(stale.expires_at)
            stale.revoke_reason = "expired"
            stale.revoked_by_login = "system"
            _event(session, stale, "EXPIRE", None, "expired", [], now)
            session.flush()
        entry = AllowBlockEntry(
            target_kind=target_kind,
            target_key=key,
            repository_id=repository_id,
            effect=effect,
            reason=reason.strip(),
            evidence_urls=evidence,
            public_message=(public_message or "").strip() or None,
            created_by_id=admin.id,
            created_by_login=admin.login,
            created_at=now,
            expires_at=expires_at,
        )
        session.add(entry)
        session.flush()
        _event(session, entry, "CREATE", admin, entry.reason, evidence, now)
        session.commit()
    except IntegrityError:
        session.rollback()
        raise ConflictError("An active entry for this target already exists.") from None
    except BaseException:
        session.rollback()
        raise
    return entry


def _utc_db(value: datetime) -> datetime:
    """Datetimes read back from SQLite lose their zone; they were stored as UTC."""
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def revoke_entry(
    session: Session,
    admin: User,
    entry_id: UUID,
    *,
    reason: str,
    evidence_urls: list[str],
    now: datetime | None = None,
) -> AllowBlockEntry:
    now = _utc(now or datetime.now(UTC))
    if not reason.strip():
        raise InvalidPolicyEntryError("A reason is required.")
    evidence = _evidence(evidence_urls)
    try:
        entry = session.get(AllowBlockEntry, entry_id, with_for_update=True)
        if entry is None:
            raise NotFoundError("Entry not found.")
        if entry.revoked_at is not None:
            raise ConflictError("The entry is already revoked.")
        if entry.expires_at is not None and _utc_db(entry.expires_at) <= now:
            raise ConflictError("The entry has already expired.")
        entry.revoked_at = now
        entry.revoked_by_id = admin.id
        entry.revoked_by_login = admin.login
        entry.revoke_reason = reason.strip()
        entry.revoke_evidence_urls = evidence
        _event(session, entry, "REVOKE", admin, entry.revoke_reason, evidence, now)
        session.commit()
    except BaseException:
        session.rollback()
        raise
    return entry


def entry_status(entry: AllowBlockEntry, now: datetime | None = None) -> str:
    now = _utc(now or datetime.now(UTC))
    if entry.revoked_at is not None:
        return "EXPIRED" if entry.revoke_reason == "expired" else "REVOKED"
    if entry.expires_at is not None and _utc_db(entry.expires_at) <= now:
        return "EXPIRED"
    return "ACTIVE"


__all__ = [
    "Decision",
    "SourceGuard",
    "active",
    "create_entry",
    "decide",
    "enforce_repository",
    "enforce_source",
    "entry_status",
    "normalize_target",
    "revoke_entry",
    "source_keys",
]
