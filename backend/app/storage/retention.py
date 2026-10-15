"""The only place snapshot files are deleted.

RETENTION RULE: a snapshot may be deleted only when no module_versions row references its
digest. Snapshots are shared: a fork's version points at the same digest as the version it
was forked from, and identical uploads share one file. Deleting a repository therefore never
deletes files directly: it removes rows, and this sweep later removes whatever no row needs.
"""

import logging
import time

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import ModuleVersion
from app.storage.snapshots import SnapshotStore

logger = logging.getLogger(__name__)


def sweep_unreferenced(
    session: Session, store: SnapshotStore, grace_seconds: float = 3600
) -> list[str]:
    """Delete snapshots that no version references. Returns the digests removed.

    Files younger than the grace period are kept: a running scan stores its snapshot before
    the version row that will reference it is committed.
    """
    stored = store.stored()
    if not stored:
        return []
    referenced = set(
        session.scalars(
            select(ModuleVersion.snapshot_sha256)
            .where(ModuleVersion.snapshot_sha256.is_not(None))
            .distinct()
        )
    )
    cutoff = time.time() - grace_seconds
    removed = []
    for digest, modified in stored.items():
        if digest not in referenced and modified <= cutoff:
            store.remove(digest)
            removed.append(digest)
    if removed:
        logger.info("action=snapshots_swept count=%s", len(removed))
    return removed
