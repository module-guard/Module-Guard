from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Query, Request

from app.api.dependencies import Limit, Offset
from app.auth.dependencies import CurrentUser, OptionalUser, verify_origin
from app.schemas.social import (
    ForkCreated,
    ForkRequest,
    RepositoryCard,
    StarState,
    UserProfile,
)

router = APIRouter(tags=["explore"])
write = [Depends(verify_origin)]


def _viewer(user) -> object | None:
    return user.id if user else None


@router.get("/repositories", response_model=list[RepositoryCard])
def explore(
    request: Request,
    user: OptionalUser,
    limit: Limit = 20,
    offset: Offset = 0,
    search: Annotated[str | None, Query(max_length=256)] = None,
    provider: Annotated[str | None, Query(max_length=100)] = None,
    sort: Literal["popular", "recent", "name"] = "popular",
):
    """Public repositories that have a stored version."""
    return request.app.state.social_service.explore(
        _viewer(user), limit, offset, search, provider, sort
    )


@router.get("/repository-providers", response_model=list[str])
def providers(request: Request):
    return request.app.state.social_service.providers()


@router.put("/repositories/{owner}/{name}/star", response_model=StarState, dependencies=write)
def star(owner: str, name: str, request: Request, user: CurrentUser):
    return request.app.state.social_service.set_star(user.id, owner, name, True)


@router.delete("/repositories/{owner}/{name}/star", response_model=StarState, dependencies=write)
def unstar(owner: str, name: str, request: Request, user: CurrentUser):
    return request.app.state.social_service.set_star(user.id, owner, name, False)


@router.post(
    "/repositories/{owner}/{name}/fork",
    status_code=201,
    response_model=ForkCreated,
    dependencies=write,
)
def fork(owner: str, name: str, payload: ForkRequest, request: Request, user: CurrentUser):
    """Create the caller's own copy. No files are copied and nothing is rescanned."""
    return request.app.state.social_service.fork(user, owner, name, payload)


@router.get("/users/{login}", response_model=UserProfile)
def profile(login: str, request: Request, user: OptionalUser):
    return request.app.state.social_service.profile(login, _viewer(user))


@router.get("/users/{login}/repositories", response_model=list[RepositoryCard])
def profile_repositories(
    login: str,
    request: Request,
    user: OptionalUser,
    kind: Literal["owned", "forked", "starred"] = "owned",
    limit: Limit = 20,
    offset: Offset = 0,
):
    return request.app.state.social_service.profile_repositories(
        login, kind, _viewer(user), limit, offset
    )
