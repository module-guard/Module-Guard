from typing import Annotated

from fastapi import Depends, Request

from app.api.dependencies import SessionDep
from app.auth import sessions
from app.core.exceptions import AuthenticationRequiredError, ForbiddenOriginError
from app.models import User


def optional_user(request: Request, session: SessionDep) -> User | None:
    return sessions.find_user(session, request.cookies.get(sessions.SESSION_COOKIE))


def current_user(user: Annotated[User | None, Depends(optional_user)]) -> User:
    if user is None:
        raise AuthenticationRequiredError("Login required.")
    return user


def verify_origin(request: Request) -> None:
    """CSRF guard for cookie-authenticated writes: the Origin must be one of our frontends."""
    if request.headers.get("origin") not in request.app.state.settings.cors_origins:
        raise ForbiddenOriginError("Request origin is not allowed.")


OptionalUser = Annotated[User | None, Depends(optional_user)]
CurrentUser = Annotated[User, Depends(current_user)]
