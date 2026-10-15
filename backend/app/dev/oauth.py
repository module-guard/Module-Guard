"""Development-only stand-in for GitHub OAuth so e2e can exercise the real login code path.

Registered only when APP_ENVIRONMENT=development; production also refuses to start if the
GitHub endpoints point anywhere but GitHub (see Settings.reject_unsafe_combinations).
"""

import re
import zlib
from urllib.parse import parse_qs

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import RedirectResponse

router = APIRouter(prefix="/api/dev/oauth", include_in_schema=False)
CODE = "dev-code"
TOKEN = "dev-token"
# Tests pick who signs in by setting this cookie before starting the login.
LOGIN_COOKIE = "mg_dev_login"
LOGIN = re.compile(r"[a-z0-9][a-z0-9-]{0,38}")


def _login(value: str | None) -> str:
    return value if value and LOGIN.fullmatch(value) else "dev-user"


@router.get("/authorize")
def authorize(request: Request, redirect_uri: str, state: str):
    # Auto-approves, but only ever sends the browser back to this API's own callback.
    expected = f"{request.app.state.settings.api_public_url}/api/auth/github/callback"
    if redirect_uri != expected:
        raise HTTPException(400, "redirect_uri mismatch")
    login = _login(request.cookies.get(LOGIN_COOKIE))
    return RedirectResponse(f"{redirect_uri}?code={CODE}-{login}&state={state}", status_code=302)


@router.post("/token")
async def token(request: Request):
    form = parse_qs((await request.body()).decode())
    code = (form.get("code") or [""])[0]
    if not code.startswith(f"{CODE}-") or not LOGIN.fullmatch(code[len(CODE) + 1 :]):
        return {"error": "bad_verification_code"}
    login = code[len(CODE) + 1 :]
    return {"access_token": f"{TOKEN}-{login}", "token_type": "bearer", "scope": "read:user"}


@router.get("/user")
def user(authorization: str = Header()):
    prefix = f"Bearer {TOKEN}-"
    login = authorization[len(prefix) :] if authorization.startswith(prefix) else ""
    if not LOGIN.fullmatch(login):
        raise HTTPException(401)
    # A stable numeric id per login, as GitHub would give.
    github_id = 1 if login == "dev-user" else 1000 + zlib.crc32(login.encode()) % 1_000_000
    name = "개발 사용자" if login == "dev-user" else login
    return {"id": github_id, "login": login, "name": name, "avatar_url": None}
