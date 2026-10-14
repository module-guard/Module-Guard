from typing import Literal
from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.visibility import require_visible_scan
from app.models.enums import Severity
from app.repositories.finding_repository import FindingRepository
from app.repositories.scan_repository import ScanRepository
from app.schemas.finding import FindingResponse

router = APIRouter(prefix="/scans", tags=["findings"], dependencies=[Depends(require_visible_scan)])


@router.get("/{scan_id}/findings", response_model=list[FindingResponse])
def list_findings(
    scan_id: UUID,
    session: SessionDep,
    limit: Limit = 20,
    offset: Offset = 0,
    severity: Severity | None = None,
    rule_id: str | None = Query(default=None, max_length=255),
    resource_type: str | None = Query(default=None, max_length=255),
    scanner: str | None = Query(default=None, max_length=64),
    scope: Literal["MODULE", "EXAMPLE"] | None = None,
):
    ScanRepository(session).get(scan_id)
    return FindingRepository(session).list(
        scan_id, limit, offset, severity, rule_id, resource_type, scanner, scope
    )
