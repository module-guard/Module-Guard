from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow


class ReputationSnapshot(Base):
    __tablename__ = "reputation_snapshots"
    __table_args__ = (
        UniqueConstraint("version_id", "bucket", name="uq_reputation_version_bucket"),
    )
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("module_versions.id", ondelete="CASCADE"), index=True
    )
    collected_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    bucket: Mapped[str] = mapped_column(String(32))
    policy_version: Mapped[str] = mapped_column(String(64))
    payload: Mapped[dict] = mapped_column(JSON_DATA)


@event.listens_for(ReputationSnapshot, "before_update")
def immutable_snapshot(mapper, connection, target):
    raise ValueError("Reputation snapshots are immutable")
