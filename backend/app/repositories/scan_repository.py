from uuid import UUID

from sqlalchemy import func, or_, select, update
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import NotFoundError
from app.models import ModuleVersion, Repository, Scan
from app.models.common import utcnow
from app.models.enums import ScanStatus

# Stages a scan passes through while a worker owns it, and every non-terminal stage.
RUNNING_STATES = (ScanStatus.COLLECTING, ScanStatus.SCANNING, ScanStatus.NORMALIZING)
ACTIVE_STATES = (ScanStatus.QUEUED, *RUNNING_STATES)


class ScanRepository:
    def __init__(self, session: Session):
        self.session = session

    def get(self, scan_id: UUID) -> Scan:
        scan = self.session.scalar(
            select(Scan).options(joinedload(Scan.module)).where(Scan.id == scan_id)
        )
        if scan is None:
            raise NotFoundError("Scan not found.")
        return scan

    def create(self, module_id: UUID) -> Scan:
        scan = Scan(module_id=module_id)
        self.session.add(scan)
        self.session.flush()
        return scan

    def claim(self, scan_id: UUID) -> bool:
        result = self.session.execute(
            update(Scan)
            .where(Scan.id == scan_id, Scan.status == ScanStatus.QUEUED)
            .values(status=ScanStatus.COLLECTING, started_at=utcnow())
        )
        self.session.commit()
        return result.rowcount == 1

    def active(self) -> list[Scan]:
        """Scans still in a non-terminal state: candidates for startup recovery."""
        return list(
            self.session.scalars(
                select(Scan)
                .options(joinedload(Scan.module))
                .where(Scan.status.in_(ACTIVE_STATES))
                .order_by(Scan.created_at)
            )
        )

    def fail_if_running(self, scan_id: UUID) -> bool:
        """Atomically move a stuck running-stage scan to FAILED. One concurrent caller wins."""
        result = self.session.execute(
            update(Scan)
            .where(Scan.id == scan_id, Scan.status.in_(RUNNING_STATES))
            .values(status=ScanStatus.FAILED, finished_at=utcnow())
        )
        self.session.commit()
        return result.rowcount == 1

    def running_publishes_for(self, user_id: UUID) -> int:
        """How many of the user's publish scans are still in flight (for the retry limit)."""
        return self.session.scalar(
            select(func.count(Scan.id))
            .join(ModuleVersion, ModuleVersion.id == Scan.module_version_id)
            .join(Repository, Repository.id == ModuleVersion.repository_id)
            .where(
                Repository.owner_id == user_id,
                Scan.status.notin_([ScanStatus.COMPLETED, ScanStatus.FAILED]),
            )
        )

    def create_retry(self, old: Scan, attempt: int) -> Scan:
        """A fresh scan for the same module and version, linked back to the failed attempt."""
        scan = Scan(
            module_id=old.module_id,
            module_version_id=old.module_version_id,
            scanner=old.scanner,
            analysis={"retry_of": str(old.id), "runtime": {"attempt": attempt}},
        )
        self.session.add(scan)
        self.session.flush()
        return scan

    def claim_retry(self, version_id: UUID, old_scan_id: UUID, new_scan_id: UUID) -> bool:
        """Point the version at the new attempt, but only if no other retry got there first.

        A plain conditional column UPDATE, so it is atomic on both SQLite and PostgreSQL:
        concurrent or repeated retries of the same failed scan can win exactly once.
        """
        result = self.session.execute(
            update(ModuleVersion)
            .where(
                ModuleVersion.id == version_id,
                or_(ModuleVersion.scan_id.is_(None), ModuleVersion.scan_id == old_scan_id),
            )
            .values(scan_id=new_scan_id)
        )
        return result.rowcount == 1
