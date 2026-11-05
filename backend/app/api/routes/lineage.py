"""Lineage and trust evidence of module versions (#26). Read-only; see docs/module-lineage.md."""

from typing import Literal

from fastapi import APIRouter, Query, Request, Response

from app.api.dependencies import Limit, Offset
from app.auth.dependencies import OptionalUser
from app.schemas.lineage import LineageDiff, LineageGraph, LineageNode
from app.services.lineage_service import LineageService

router = APIRouter(tags=["lineage"])

Version = Query(default=None, max_length=100)


def _service(request: Request, response: Response) -> LineageService:
    # Visibility depends on the viewer, so nothing here may be cached by shared caches.
    response.headers["Cache-Control"] = "no-store"
    return LineageService(request.app.state.session_factory)


@router.get("/repositories/{owner}/{name}/lineage", response_model=LineageNode)
def lineage(
    owner: str,
    name: str,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Version,
):
    return _service(request, response).node(owner, name, version, user.id if user else None)


@router.get("/repositories/{owner}/{name}/lineage/graph", response_model=LineageGraph)
def lineage_graph(
    owner: str,
    name: str,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Version,
    direction: Literal["ancestors", "descendants"] = "ancestors",
    limit: Limit = 20,
    offset: Offset = 0,
):
    return _service(request, response).graph(
        owner, name, version, user.id if user else None, direction, limit, offset
    )


@router.get("/repositories/{owner}/{name}/lineage/diff", response_model=LineageDiff)
def lineage_diff(
    owner: str,
    name: str,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Version,
    limit: Limit = 50,
    offset: Offset = 0,
):
    return _service(request, response).diff(
        owner, name, version, user.id if user else None, limit, offset
    )
