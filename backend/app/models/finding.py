from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Index, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.common import JSON_DATA, enum_type, utcnow
from app.models.enums import Severity

if TYPE_CHECKING:
    from app.models.scan import Scan


class Finding(Base):
    __tablename__ = "findings"
    __table_args__ = (Index("ix_findings_scan_severity", "scan_id", "severity"),)

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    scan_id: Mapped[UUID] = mapped_column(ForeignKey("scans.id", ondelete="CASCADE"), index=True)
    rule_id: Mapped[str] = mapped_column(String(255), index=True)
    scanner: Mapped[str] = mapped_column(String(64), default="checkov")
    severity: Mapped[Severity] = mapped_column(enum_type(Severity), index=True)
    title: Mapped[str] = mapped_column(Text)
    resource: Mapped[str | None] = mapped_column(Text)
    resource_type: Mapped[str | None] = mapped_column(String(255), index=True)
    file_path: Mapped[str | None] = mapped_column(Text)
    line_start: Mapped[int | None]
    line_end: Mapped[int | None]
    description: Mapped[str | None] = mapped_column(Text)
    guideline: Mapped[str | None] = mapped_column(Text)
    check_result: Mapped[str] = mapped_column(String(32), default="FAILED")
    raw_data: Mapped[dict[str, Any]] = mapped_column(JSON_DATA, default=dict)
    scope: Mapped[str] = mapped_column(String(16), default="MODULE", index=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    scan: Mapped["Scan"] = relationship(back_populates="findings")
