from uuid import UUID

from fastapi import APIRouter, Depends, Query

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.visibility import require_visible_scan
from app.parsers.domain import EntityType
from app.repositories.graph_repository import GraphRepository
from app.repositories.scan_repository import ScanRepository
from app.schemas.graph import DependencyResponse, EntityResponse, GraphResponse
from app.services.graph_service import GraphService

router = APIRouter(prefix="/scans", tags=["analysis"], dependencies=[Depends(require_visible_scan)])


@router.get("/{scan_id}/entities", response_model=list[EntityResponse])
def list_entities(
    scan_id: UUID,
    session: SessionDep,
    limit: Limit = 20,
    offset: Offset = 0,
    entity_type: EntityType | None = None,
    resource_type: str | None = Query(default=None, max_length=255),
):
    ScanRepository(session).get(scan_id)
    return GraphRepository(session).entities(scan_id, limit, offset, entity_type, resource_type)


@router.get("/{scan_id}/graph", response_model=GraphResponse)
def get_graph(scan_id: UUID, session: SessionDep):
    return GraphService(session).graph(scan_id)


@router.get("/{scan_id}/dependencies", response_model=DependencyResponse)
def get_dependencies(scan_id: UUID, session: SessionDep):
    return GraphService(session).dependencies(scan_id)
