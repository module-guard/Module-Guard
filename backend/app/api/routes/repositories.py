from typing import Annotated

from fastapi import APIRouter, Depends, Query, Request, Response
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import CurrentUser, OptionalUser, verify_origin
from app.core.exceptions import ScanQueueFullError
from app.schemas.home import HomeResponse
from app.schemas.publish import (
    MyRepository,
    RepositoryCreate,
    RepositoryCreated,
    RepositoryDetail,
    RepositoryEdit,
    VersionCreate,
)

router = APIRouter(tags=["repositories"])


@router.post(
    "/repositories",
    status_code=201,
    response_model=RepositoryCreated,
    dependencies=[Depends(verify_origin)],
)
async def create_repository(
    payload: RepositoryCreate, request: Request, response: Response, user: CurrentUser
):
    """Register a prepared upload as a new repository and start its scan."""
    service = request.app.state.publish_service
    scans = request.app.state.scan_service
    created = await run_in_threadpool(service.register, user, payload, scans.scanner.name)
    try:
        request.app.state.dispatcher.submit(created.scan_id)
    except ScanQueueFullError:
        await run_in_threadpool(
            scans.mark_failed, created.scan_id, "Scan queue is unavailable. Submit a new scan."
        )
        raise
    response.headers["Location"] = f"/api/scans/{created.scan_id}"
    return created


@router.post(
    "/repositories/{name}/versions",
    status_code=201,
    response_model=RepositoryCreated,
    dependencies=[Depends(verify_origin)],
)
async def create_version(
    name: str, payload: VersionCreate, request: Request, response: Response, user: CurrentUser
):
    """Add the next version of the caller's repository from a prepared upload."""
    service = request.app.state.publish_service
    scans = request.app.state.scan_service
    created = await run_in_threadpool(
        service.register_version, user, name, payload, scans.scanner.name
    )
    try:
        request.app.state.dispatcher.submit(created.scan_id)
    except ScanQueueFullError:
        await run_in_threadpool(
            scans.mark_failed, created.scan_id, "Scan queue is unavailable. Submit a new scan."
        )
        raise
    response.headers["Location"] = f"/api/scans/{created.scan_id}"
    return created


@router.get("/repositories/{owner}/{name}", response_model=RepositoryDetail)
def get_repository(
    owner: str,
    name: str,
    request: Request,
    user: OptionalUser,
    version: Annotated[str | None, Query(max_length=100)] = None,
):
    return request.app.state.publish_service.detail(owner, name, user.id if user else None, version)


@router.get("/me/home", response_model=HomeResponse)
def my_home(request: Request, user: CurrentUser):
    """The signed-in user's repositories, what needs a look and recent events."""
    return request.app.state.home_service.home(user.id)


@router.get("/me/repositories", response_model=list[MyRepository])
def my_repositories(request: Request, user: CurrentUser):
    return request.app.state.publish_service.mine(user.id)


@router.delete("/repositories/{name}", status_code=204, dependencies=[Depends(verify_origin)])
def delete_repository(
    name: str,
    request: Request,
    user: CurrentUser,
    confirm: Annotated[str | None, Query(max_length=100)] = None,
):
    """Delete one of the caller's repositories. A published one needs ?confirm=<its name>."""
    request.app.state.publish_service.delete(user.id, name, confirm)


@router.patch("/repositories/{name}", status_code=204, dependencies=[Depends(verify_origin)])
def edit_repository(name: str, payload: RepositoryEdit, request: Request, user: CurrentUser):
    request.app.state.publish_service.edit(user.id, name, payload)
