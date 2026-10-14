from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow


class ParsedEntity(Base):
    __tablename__ = "parsed_entities"
    __table_args__ = (
        UniqueConstraint("scan_id", "address", name="uq_entity_scan_address"),
        UniqueConstraint("scan_id", "id", name="uq_entity_scan_id"),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scan_id: Mapped[UUID] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    entity_type: Mapped[str] = mapped_column(String(32), index=True)
    address: Mapped[str] = mapped_column(Text, index=True)
    name: Mapped[str] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(255), index=True)
    file_path: Mapped[str] = mapped_column(Text)
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    attributes: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    entity_metadata: Mapped[dict[str, Any]] = mapped_column("metadata", JSON_DATA, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
