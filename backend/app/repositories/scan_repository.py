from uuid import UUID

from sqlalchemy import select, update
from sqlalchemy.orm import Session, joinedload

from app.core.exceptions import NotFoundError
from app.models import Scan
from app.models.common import utcnow
from app.models.enums import ScanStatus


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
