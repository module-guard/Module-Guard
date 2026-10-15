import logging
from typing import Annotated
from urllib.parse import urlencode

from fastapi import APIRouter, Depends, Query, Request, Response
from fastapi.responses import RedirectResponse
from starlette.concurrency import run_in_threadpool

from app.auth import sessions, state
from app.auth.dependencies import OptionalUser, verify_origin
from app.auth.oauth import OAuthError
from app.core.exceptions import LoginUnavailableError
from app.schemas.auth import MeResponse

logger = logging.getLogger(__name__)
router = APIRouter(tags=["auth"])
STATE_PATH = "/api/auth"


def _cookie_options(request: Request) -> dict:
    return {
        "httponly": True,
        "samesite": "lax",
        "secure": request.app.state.settings.cookie_secure,
    }


def _to_frontend(request: Request, path: str, error: str | None = None) -> RedirectResponse:
    target = f"{request.app.state.settings.frontend_url}{path}"
    if error:
        target += ("&" if "?" in path else "?") + urlencode({"login_error": error})
    response = RedirectResponse(target, status_code=302)
    # The state is single use: it is discarded whatever the outcome.
    response.delete_cookie(state.STATE_COOKIE, path=STATE_PATH, **_cookie_options(request))
    return response


@router.get("/auth/github/login", status_code=302, response_class=RedirectResponse)
def login(request: Request, next: str | None = None):
    settings = request.app.state.settings
    if not settings.auth_configured:
        raise LoginUnavailableError("GitHub login is not configured on this server.")
    value, cookie = state.issue(settings.session_secret.get_secret_value(), state.safe_next(next))
    response = RedirectResponse(request.app.state.oauth.authorize_url(value), status_code=302)
    response.set_cookie(
        state.STATE_COOKIE,
        cookie,
        max_age=state.STATE_TTL_SECONDS,
        path=STATE_PATH,
        **_cookie_options(request),
    )
    return response


@router.get("/auth/github/callback", status_code=302, response_class=RedirectResponse)
async def callback(
    request: Request,
    code: str | None = None,
    state_value: Annotated[str | None, Query(alias="state")] = None,
):
    settings = request.app.state.settings
    if not settings.auth_configured:
        raise LoginUnavailableError("GitHub login is not configured on this server.")
    next_path = state.verify(
        settings.session_secret.get_secret_value(),
        request.cookies.get(state.STATE_COOKIE),
        state_value,
    )
    if next_path is None:
        logger.warning("action=login_rejected reason=state")
        return _to_frontend(request, state.DEFAULT_NEXT, "state")
    if not code:
        # The user declined on GitHub, or the provider reported an error.
        return _to_frontend(request, next_path, "denied")
    try:
        profile = await request.app.state.oauth.fetch_profile(code)
    except OAuthError as error:
        logger.warning("action=login_failed reason=%s", error)
        return _to_frontend(request, next_path, "github")

    def persist() -> str:
        with request.app.state.session_factory() as session:
            user = sessions.upsert_user(session, profile, settings.reserved_logins)
            token = sessions.create(
                session,
                user,
                settings.session_ttl_seconds,
                replaces=request.cookies.get(sessions.SESSION_COOKIE),
            )
            session.commit()
            return token

    token = await run_in_threadpool(persist)
    response = _to_frontend(request, next_path)
    response.set_cookie(
        sessions.SESSION_COOKIE,
        token,
        max_age=settings.session_ttl_seconds,
        path="/",
        **_cookie_options(request),
    )
    return response


@router.post("/auth/logout", status_code=204, dependencies=[Depends(verify_origin)])
def logout(request: Request, response: Response):
    with request.app.state.session_factory() as session:
        sessions.revoke(session, request.cookies.get(sessions.SESSION_COOKIE))
        session.commit()
    response.delete_cookie(sessions.SESSION_COOKIE, path="/", **_cookie_options(request))


@router.get("/me", response_model=MeResponse)
def me(request: Request, user: OptionalUser, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return MeResponse(user=user, login_available=request.app.state.settings.auth_configured)
