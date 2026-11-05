"""Immutable lineage of module versions (#26): where a version came from and who published it.

One row per repository version, written once when the version is created and never changed.
The row deliberately holds plain ids rather than foreign keys to the version, repository and
publisher: deleting any of those must not rewrite history. Whether a row may be shown is
decided at read time from the live version and repository (see services/lineage_service.py),
so a deleted or hidden version keeps its record but is never revealed.

The manifest is a copy of the #9 manifest of the version (format 1). It is kept here so the
comparison with a parent still works, and stays the same, after the parent version is gone.
"""

from datetime import datetime
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import DateTime, ForeignKey, String, event
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.models.common import JSON_DATA, utcnow

# How a version relates to its parent record.
ORIGINAL = "ORIGINAL"  # first version of a repository, collected from GitHub or an upload
VERSION = "VERSION"  # a later version of the same repository; parent is the previous version
FORK = "FORK"  # copied verbatim from another repository's version by "가져오기"
RELATIONS = (ORIGINAL, VERSION, FORK)

# Text the publisher agreed to by publishing. Bump when the wording in
# docs/module-lineage.md changes; old rows keep the version they were published under.
DECLARATION_VERSION = "publisher-declaration/1"


class LineageRecord(Base):
    __tablename__ = "lineage_records"

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    # The version this row describes. One row per version; no FK on purpose (see module doc).
    version_id: Mapped[UUID] = mapped_column(unique=True)
    repository_id: Mapped[UUID] = mapped_column(index=True)
    # The record this version derives from. Null for ORIGINAL. Records are never deleted,
    # so the chain survives deletions of the versions it describes.
    parent_id: Mapped[UUID | None] = mapped_column(
        ForeignKey("lineage_records.id", ondelete="RESTRICT"), index=True
    )
    relation: Mapped[str] = mapped_column(String(16))
    # Copied from the version at creation time (#9 integrity data; nothing is recomputed).
    manifest: Mapped[dict[str, Any] | None] = mapped_column(JSON_DATA)
    manifest_sha256: Mapped[str | None] = mapped_column(String(64))
    source_url: Mapped[str | None] = mapped_column(String(2048))
    commit_hash: Mapped[str | None] = mapped_column(String(64))
    module_path: Mapped[str] = mapped_column(String(2048), default=".")
    # The account that published this version: stable users.id, not the login, which can change.
    # The publisher is not asserted to be the author of the code.
    publisher_id: Mapped[UUID | None] = mapped_column()
    published_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    # Null for rows filled in by migration 0011 for versions published before it existed.
    declaration_version: Mapped[str | None] = mapped_column(String(64))
    backfilled: Mapped[bool] = mapped_column(default=False)


class LineageImmutableError(RuntimeError):
    pass


@event.listens_for(LineageRecord, "before_update")
def _refuse_update(mapper, connection, target):  # noqa: ARG001
    raise LineageImmutableError("Lineage records are written once and never changed.")


@event.listens_for(LineageRecord, "before_delete")
def _refuse_delete(mapper, connection, target):  # noqa: ARG001
    raise LineageImmutableError("Lineage records are never deleted.")
