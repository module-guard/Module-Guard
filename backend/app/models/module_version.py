from datetime import datetime
from typing import TYPE_CHECKING, Any
from uuid import UUID, uuid4

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow

if TYPE_CHECKING:
    from app.models.repository import Repository


class ModuleVersion(Base):
    """Immutable record of collected content: snapshot, extracted interface and its scan.

    repository_id is null for scans that ran outside a repository (plain scans, fixtures).
    """

    __tablename__ = "module_versions"
    __table_args__ = (UniqueConstraint("repository_id", "version", name="uq_version_repository"),)
    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    repository_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("repositories.id", ondelete="CASCADE"), index=True
    )
    version: Mapped[str | None] = mapped_column(String(100))
    snapshot_sha256: Mapped[str | None] = mapped_column(String(64), index=True)
    size_bytes: Mapped[int | None] = mapped_column(BigInteger)
    file_count: Mapped[int | None]
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    # The tag that was fetched, when the source was pinned to one (mirrors).
    source_ref: Mapped[str | None] = mapped_column(String(100))
    readme: Mapped[str | None] = mapped_column(Text)
    # None means "unknown" (parse failed); an empty list means the module declares none.
    variables: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_DATA)
    outputs: Mapped[list[dict[str, Any]] | None] = mapped_column(JSON_DATA)
    license: Mapped[str | None] = mapped_column(String(64))
    # Distinct CRITICAL / HIGH rules in the module body: what the security badge shows.
    critical_rules: Mapped[int] = mapped_column(default=0)
    high_rules: Mapped[int] = mapped_column(default=0)
    scan_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("scans.id", ondelete="SET NULL"), index=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, index=True
    )
    repository: Mapped["Repository | None"] = relationship()
