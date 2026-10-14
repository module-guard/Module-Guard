from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.common import JSON_DATA, enum_type, utcnow
from app.models.enums import ScanStatus

if TYPE_CHECKING:
    from app.models.finding import Finding
    from app.models.module import Module
    from app.models.module_version import ModuleVersion


class Scan(Base):
    __tablename__ = "scans"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    module_id: Mapped[UUID] = mapped_column(
        ForeignKey("modules.id", ondelete="CASCADE"), index=True
    )
    # Set when the scan belongs to a version; scans and versions reference each other.
    module_version_id: Mapped[UUID | None] = mapped_column(
        ForeignKey(
            "module_versions.id",
            ondelete="SET NULL",
            name="fk_scan_module_version",
            use_alter=True,
        ),
        index=True,
    )
    status: Mapped[ScanStatus] = mapped_column(
        enum_type(ScanStatus), default=ScanStatus.QUEUED, index=True
    )
    scanner: Mapped[str] = mapped_column(String(64), default="checkov")
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    branch: Mapped[str | None] = mapped_column(String(255))
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    duration_ms: Mapped[int | None] = mapped_column(Integer)
    total_findings: Mapped[int] = mapped_column(default=0)
    critical_count: Mapped[int] = mapped_column(default=0)
    high_count: Mapped[int] = mapped_column(default=0)
    medium_count: Mapped[int] = mapped_column(default=0)
    low_count: Mapped[int] = mapped_column(default=0)
    info_count: Mapped[int] = mapped_column(default=0)
    unknown_count: Mapped[int] = mapped_column(default=0)
    partial_result: Mapped[bool] = mapped_column(default=False)
    warnings: Mapped[list[str]] = mapped_column(JSON_DATA, default=list)
    error_message: Mapped[str | None] = mapped_column(Text)
    analysis: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA)
    # The *_count columns describe the module body; examples and tests are summarised here.
    scope_summary: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA)
    raw_result: Mapped[Any | None] = mapped_column(JSON_DATA, deferred=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    module: Mapped["Module"] = relationship(back_populates="scans")
    module_version: Mapped["ModuleVersion | None"] = relationship(foreign_keys=[module_version_id])
    findings: Mapped[list["Finding"]] = relationship(back_populates="scan", passive_deletes=True)
