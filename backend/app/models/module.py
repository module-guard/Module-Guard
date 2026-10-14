from datetime import datetime
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from sqlalchemy import DateTime, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.common import enum_type, utcnow
from app.models.enums import SourceType

if TYPE_CHECKING:
    from app.models.scan import Scan


class Module(Base):
    __tablename__ = "modules"
    __table_args__ = (UniqueConstraint("source_type", "source_url", name="uq_module_source"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    name: Mapped[str] = mapped_column(String(255))
    source_type: Mapped[SourceType] = mapped_column(enum_type(SourceType))
    source_url: Mapped[str] = mapped_column(String(2048))
    repository_owner: Mapped[str | None] = mapped_column(String(255))
    repository_name: Mapped[str | None] = mapped_column(String(255))
    default_branch: Mapped[str | None] = mapped_column(String(255))
    provider: Mapped[str | None] = mapped_column(String(100))
    latest_version: Mapped[str | None] = mapped_column(String(100))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    scans: Mapped[list["Scan"]] = relationship(back_populates="module", passive_deletes=True)
