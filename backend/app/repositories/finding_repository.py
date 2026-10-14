from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Finding
from app.models.enums import Severity
from app.schemas.finding import NormalizedFinding


class FindingRepository:
    def __init__(self, session: Session):
        self.session = session

    def add_many(self, scan_id: UUID, findings: list[NormalizedFinding]) -> None:
        self.session.add_all(
            Finding(scan_id=scan_id, **finding.model_dump()) for finding in findings
        )

    def list(
        self,
        scan_id: UUID,
        limit: int,
        offset: int,
        severity: Severity | None = None,
        rule_id: str | None = None,
        resource_type: str | None = None,
        scanner: str | None = None,
        scope: str | None = None,
    ) -> list[Finding]:
        query = select(Finding).where(Finding.scan_id == scan_id)
        for column, value in (
            (Finding.scope, scope),
            (Finding.severity, severity),
            (Finding.rule_id, rule_id),
            (Finding.resource_type, resource_type),
            (Finding.scanner, scanner),
        ):
            if value is not None:
                query = query.where(column == value)
        return list(
            self.session.scalars(
                query.order_by(Finding.created_at.desc(), Finding.id.desc())
                .limit(limit)
                .offset(offset)
            )
        )
