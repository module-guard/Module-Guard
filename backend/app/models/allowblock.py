from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    String,
    Text,
    event,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow

TARGET_KINDS = ("repository", "github_repository", "github_owner")
EFFECTS = ("ALLOW", "BLOCK")
ACTIONS = ("CREATE", "REVOKE", "EXPIRE")


def _in(column: str, values: tuple[str, ...]) -> str:
    return f"{column} IN ({', '.join(repr(v) for v in values)})"


class AllowBlockEntry(Base):
    """One allow or block decision about a source. Never deleted: it is revoked or expires."""

    __tablename__ = "allowblock_entries"
    __table_args__ = (
        CheckConstraint(_in("target_kind", TARGET_KINDS), name="ck_allowblock_target_kind"),
        CheckConstraint(_in("effect", EFFECTS), name="ck_allowblock_effect"),
        CheckConstraint(
            "expires_at IS NULL OR expires_at > created_at", name="ck_allowblock_expiry_order"
        ),
        # One live entry per target and effect. Revoked or expired-and-closed rows are history.
        Index(
            "uq_allowblock_active_target",
            "target_kind",
            "target_key",
            "effect",
            unique=True,
            sqlite_where=text("revoked_at IS NULL"),
            postgresql_where=text("revoked_at IS NULL"),
        ),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    target_kind: Mapped[str] = mapped_column(String(32))
    target_key: Mapped[str] = mapped_column(String(255), index=True)
    repository_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), index=True
    )
    effect: Mapped[str] = mapped_column(String(8))
    reason: Mapped[str] = mapped_column(Text)
    evidence_urls: Mapped[list] = mapped_column(JSON_DATA)
    public_message: Mapped[str | None] = mapped_column(String(500))
    created_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    created_by_login: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    revoked_by_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    revoked_by_login: Mapped[str | None] = mapped_column(String(64))
    revoke_reason: Mapped[str | None] = mapped_column(Text)
    revoke_evidence_urls: Mapped[list | None] = mapped_column(JSON_DATA)


class AllowBlockEvent(Base):
    """Append-only audit trail of an entry."""

    __tablename__ = "allowblock_events"
    __table_args__ = (CheckConstraint(_in("action", ACTIONS), name="ck_allowblock_event_action"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    entry_id: Mapped[UUID] = mapped_column(ForeignKey("allowblock_entries.id"), index=True)
    action: Mapped[str] = mapped_column(String(8))
    actor_id: Mapped[UUID | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"))
    actor_login: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    reason: Mapped[str] = mapped_column(Text)
    evidence_urls: Mapped[list] = mapped_column(JSON_DATA)
    snapshot: Mapped[dict] = mapped_column(JSON_DATA)


@event.listens_for(AllowBlockEvent, "before_update")
def _events_are_append_only(mapper, connection, target):
    raise ValueError("Allow/block events are append-only")


@event.listens_for(AllowBlockEvent, "before_delete")
def _events_are_never_deleted(mapper, connection, target):
    raise ValueError("Allow/block events are append-only")
