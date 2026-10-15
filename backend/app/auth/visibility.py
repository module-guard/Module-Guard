"""Who may see a scan. Scans of a private repository are visible only to its owner."""

from uuid import UUID

from fastapi import Request
from sqlalchemy import and_, exists, or_, select
from sqlalchemy.orm import aliased

from app.api.dependencies import SessionDep
from app.auth.dependencies import OptionalUser
from app.core.exceptions import NotFoundError
from app.models import ModuleVersion, Repository, Scan
from app.models.enums import Visibility


def repository_visible_to(viewer_id: UUID | None):
    """SQL condition on Repository rows.

    Others see a repository only when it is public and has at least one stored version:
    private ones, attempts still running and failed attempts stay with the owner.
    """
    stored = aliased(ModuleVersion)
    published = exists(
        select(stored.id).where(
            stored.repository_id == Repository.id, stored.snapshot_sha256.is_not(None)
        )
    )
    shown = and_(Repository.visibility == Visibility.PUBLIC, published)
    return shown if viewer_id is None else or_(Repository.owner_id == viewer_id, shown)


def scan_visible_to(viewer_id: UUID | None):
    """SQL condition on Scan rows.

    Scans outside any repository are public. A scan used by repositories (its own, and forks
    that share it through ModuleVersion.scan_id) is visible when at least one of them is
    visible to the viewer, so a fork keeps its security detail whatever happens to the origin.
    """
    using = aliased(ModuleVersion)
    linked = (
        select(using.id)
        .join(Repository, Repository.id == using.repository_id)
        .where(or_(using.scan_id == Scan.id, using.id == Scan.module_version_id))
    )
    return or_(~exists(linked), exists(linked.where(repository_visible_to(viewer_id))))


def require_visible_scan(request: Request, session: SessionDep, user: OptionalUser) -> None:
    """Router-level guard for every /scans/{scan_id}/… route. Hidden scans look nonexistent."""
    raw = request.path_params.get("scan_id")
    if raw is None:
        return
    try:
        scan_id = UUID(raw)
    except ValueError:
        return  # Path validation reports the malformed id.
    viewer_id = user.id if user else None
    found = session.scalar(select(Scan.id).where(Scan.id == scan_id, scan_visible_to(viewer_id)))
    if found is None:
        raise NotFoundError("Scan not found.")
