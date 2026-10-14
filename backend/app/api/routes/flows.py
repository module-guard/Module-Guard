from uuid import UUID

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.visibility import require_visible_scan
from app.parsers.domain import EntityType
from app.schemas.flow import EntityFlowResponse, FlowResponse, FlowTraceResponse
from app.services.flow_service import FlowService

router = APIRouter(
    prefix="/scans", tags=["data-flow"], dependencies=[Depends(require_visible_scan)]
)


@router.get("/{scan_id}/flows", response_model=FlowResponse)
def flows(
    scan_id: UUID,
    session: SessionDep,
    limit: Limit = 20,
    offset: Offset = 0,
    source: str | None = Query(None, max_length=8192),
    target: str | None = Query(None, max_length=8192),
    edge_type: str | None = Query(None, max_length=80),
    entity_type: EntityType | None = None,
):
    return FlowService(session).flows(
        scan_id,
        source=source,
        target=target,
        edge_type=edge_type,
        entity_type=entity_type,
        limit=limit,
        offset=offset,
    )


@router.get("/{scan_id}/flows/{entity_address}", response_model=EntityFlowResponse)
def entity_flows(scan_id: UUID, entity_address: str, session: SessionDep):
    return FlowService(session).entity(scan_id, entity_address)


@router.get("/{scan_id}/flow-trace", response_model=FlowTraceResponse)
def trace(
    scan_id: UUID,
    request: Request,
    session: SessionDep,
    source: str = Query(alias="from", min_length=1, max_length=8192),
    target: str = Query(alias="to", min_length=1, max_length=8192),
    max_depth: int = Query(10, ge=1, le=30),
):
    return FlowService(session, request.app.state.settings).trace(
        scan_id, source, target, max_depth
    )
