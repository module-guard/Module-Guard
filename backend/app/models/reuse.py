"""Append-only reuse decisions; no source identifiers for misses."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow


class ReuseRecord(Base):
    __tablename__ = "scan_reuse"
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    target_scan_id: Mapped[UUID] = mapped_column(
        ForeignKey("scans.id", ondelete="CASCADE"), unique=True
    )
    source_scan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), index=True
    )
    source_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("module_versions.id", ondelete="SET NULL")
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    source_finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reused: Mapped[bool] = mapped_column(default=False)
    reason: Mapped[str] = mapped_column(String(80))
    key: Mapped[str | None] = mapped_column(String(64))
    policy_version: Mapped[str] = mapped_column(String(80))
    conditions: Mapped[dict] = mapped_column(JSON_DATA, default=dict)
    verification: Mapped[dict] = mapped_column(JSON_DATA, default=dict)
    source_duration_ms: Mapped[int | None]
    lookup_duration_ms: Mapped[int]


@event.listens_for(ReuseRecord, "before_update")
def immutable_record(mapper, connection, target):
    raise ValueError("Reuse records are immutable")
