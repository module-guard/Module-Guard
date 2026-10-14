from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.visibility import require_visible_scan
from app.correlation.domain import Confidence
from app.models.enums import Severity
from app.repositories.correlation_repository import CorrelationRepository
from app.repositories.scan_repository import ScanRepository
from app.schemas.correlation import CorrelationDetail
from app.schemas.finding import FindingResponse
from app.services.correlation_service import CorrelationService

router = APIRouter(
    prefix="/scans", tags=["correlation"], dependencies=[Depends(require_visible_scan)]
)


@router.get("/{scan_id}/correlations", response_model=list[FindingResponse])
def correlations(
    scan_id: UUID,
    session: SessionDep,
    limit: Limit = 20,
    offset: Offset = 0,
    severity: Severity | None = None,
    rule_id: str | None = Query(None, max_length=255),
    source: str | None = Query(None, max_length=8192),
    sink: str | None = Query(None, max_length=8192),
    confidence: Confidence | None = None,
):
    ScanRepository(session).get(scan_id)
    return CorrelationRepository(session).list(
        scan_id,
        limit,
        offset,
        severity=severity,
        rule_id=rule_id,
        source=source,
        sink=sink,
        confidence=confidence,
    )


@router.get("/{scan_id}/correlations/{finding_id}", response_model=CorrelationDetail)
def detail(scan_id: UUID, finding_id: UUID, session: SessionDep):
    return CorrelationService(session).detail(scan_id, finding_id)
