"""Immutable final ZIP, including generated root Terraform files (never sensitive values)."""

from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, LargeBinary, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import utcnow


class DownloadArtifact(Base):
    __tablename__ = "download_artifacts"
    __table_args__ = (UniqueConstraint("version_id", "request_sha256", name="uq_artifact_request"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    version_id: Mapped[UUID] = mapped_column(
        ForeignKey("module_versions.id", ondelete="CASCADE"), index=True
    )
    request_sha256: Mapped[str] = mapped_column(String(64))
    zip_sha256: Mapped[str] = mapped_column(String(64))
    artifact_size: Mapped[int]
    # DB blob provides atomic publication and a single byte snapshot for verify + send.
    content: Mapped[bytes] = mapped_column(LargeBinary, deferred=True)
    integrity_status: Mapped[str] = mapped_column(String(16), default="VERIFIED")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
