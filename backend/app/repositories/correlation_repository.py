from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.core.exceptions import NotFoundError
from app.correlation.domain import Confidence
from app.models import Finding, GraphEdge
from app.models.enums import Severity


class CorrelationRepository:
    def __init__(self, session: Session):
        self.session = session

    def list(
        self,
        scan_id: UUID,
        limit: int,
        offset: int,
        *,
        severity: Severity | None = None,
        rule_id: str | None = None,
        source: str | None = None,
        sink: str | None = None,
        confidence: Confidence | None = None,
    ) -> list[Finding]:
        query = select(Finding).where(Finding.scan_id == scan_id, Finding.scanner == "correlation")
        for column, value in (
            (Finding.severity, severity),
            (Finding.rule_id, rule_id),
            (Finding.raw_data["source"].as_string(), source),
            (Finding.raw_data["sink"].as_string(), sink),
            (Finding.raw_data["confidence"].as_string(), confidence),
        ):
            if value is not None:
                query = query.where(column == value)
        return list(
            self.session.scalars(
                query.order_by(Finding.created_at.desc(), Finding.id).limit(limit).offset(offset)
            )
        )

    def get(self, scan_id: UUID, finding_id: UUID) -> Finding:
        finding = self.session.scalar(
            select(Finding).where(
                Finding.scan_id == scan_id,
                Finding.id == finding_id,
                Finding.scanner == "correlation",
            )
        )
        if finding is None:
            raise NotFoundError("Correlated finding not found in this scan.")
        return finding

    def related(self, scan_id: UUID, ids: list[str]) -> list[Finding]:
        return (
            list(
                self.session.scalars(
                    select(Finding).where(
                        Finding.scan_id == scan_id, Finding.id.in_([UUID(i) for i in ids])
                    )
                )
            )
            if ids
            else []
        )

    def edges(self, scan_id: UUID, ids: list[str]) -> list[GraphEdge]:
        return (
            list(
                self.session.scalars(
                    select(GraphEdge).where(
                        GraphEdge.scan_id == scan_id, GraphEdge.id.in_([UUID(i) for i in ids])
                    )
                )
            )
            if ids
            else []
        )
