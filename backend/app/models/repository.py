from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.common import enum_type, utcnow
from app.models.enums import OriginType, Visibility

if TYPE_CHECKING:
    from app.models.user import User


class Repository(Base):
    """What users call a module: one owner, one slug, many immutable versions."""

    __tablename__ = "repositories"
    __table_args__ = (UniqueConstraint("owner_id", "name", name="uq_repository_owner_name"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    owner_id: Mapped[UUID] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(100))
    description: Mapped[str | None] = mapped_column(Text)
    provider: Mapped[str | None] = mapped_column(String(100))
    visibility: Mapped[Visibility] = mapped_column(
        enum_type(Visibility), default=Visibility.PUBLIC, index=True
    )
    forked_from_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="SET NULL"), index=True
    )
    origin_type: Mapped[OriginType] = mapped_column(enum_type(OriginType))
    origin_url: Mapped[str | None] = mapped_column(String(2048))
    star_count: Mapped[int] = mapped_column(default=0)
    fork_count: Mapped[int] = mapped_column(default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    owner: Mapped["User"] = relationship()
