import logging
import time
from datetime import timedelta
from urllib.parse import parse_qs, urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError
from sqlalchemy import func, select

from app.auth import state as oauth_state
from app.auth.oauth import GitHubOAuth, GitHubProfile, OAuthError
from app.core.config import Settings
from app.main import create_app
from app.models import User, UserSession
from app.models.common import utcnow

ORIGIN = "http://localhost:3000"
SECRETS = {"client": "client-secret-value", "session": "session-secret-value"}


class FakeGitHub(GitHubOAuth):
    """Real URL building, canned profile: no network."""

    def __init__(self, settings):
        super().__init__(settings)
        self.profile = GitHubProfile(42, "octocat", "The Octocat", "https://avatars.test/42")
        self.codes = []

    async def fetch_profile(self, code):
        self.codes.append(code)
        if code == "bad":
            raise OAuthError("token")
        return self.profile


@pytest.fixture
def auth_settings(settings):
    return settings.model_copy(
        update={
            "github_client_id": "client-id",
            "github_client_secret": SECRETS["client"],
            "session_secret": SECRETS["session"],
        }
    )


def configured(settings):
    # model_copy skips validation; rebuild so SecretStr fields and validators apply.
    return Settings(**{**settings.model_dump(), "_env_file": None})


@pytest.fixture
def app(auth_settings, sessions):
    settings = configured(auth_settings)
    github = FakeGitHub(settings)
    application = create_app(settings, sessions, oauth=github)
    application.state.github = github
    return application


@pytest.fixture
def client(app):
    with TestClient(app, follow_redirects=False) as test_client:
        yield test_client


def start(client, next_path=None):
    response = client.get("/api/auth/github/login", params={"next": next_path} if next_path else {})
    assert response.status_code == 302
    return response, parse_qs(urlsplit(response.headers["location"]).query)


def sign_in(client, next_path=None, code="good"):
    _, query = start(client, next_path)
    return client.get(
        "/api/auth/github/callback", params={"code": code, "state": query["state"][0]}
    )


def replace_session_cookie(client, value):
    """Swap the browser's session cookie for another value (replay / planted cookie)."""
    for cookie in [c for c in client.cookies.jar if c.name == "mg_session"]:
        client.cookies.jar.clear(cookie.domain, cookie.path, cookie.name)
    client.cookies.set("mg_session", value, domain="testserver.local", path="/")


def set_cookie(response, name):
    return next(h for h in response.headers.get_list("set-cookie") if h.startswith(f"{name}="))


def test_login_redirects_to_github_with_bound_state(client):
    response, query = start(client)
    location = urlsplit(response.headers["location"])
    assert f"{location.scheme}://{location.netloc}{location.path}" == (
        "https://github.com/login/oauth/authorize"
    )
    assert query["client_id"] == ["client-id"] and query["scope"] == ["read:user"]
    assert query["redirect_uri"] == ["http://localhost:8000/api/auth/github/callback"]
    assert len(query["state"][0]) >= 32
    cookie = set_cookie(response, oauth_state.STATE_COOKIE)
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Max-Age=600" in cookie
    assert "Path=/api/auth" in cookie and "Secure" not in cookie
    assert SECRETS["client"] not in response.headers["location"]
    # Every attempt gets its own state.
    assert start(client)[1]["state"] != query["state"]


def test_successful_login_creates_user_and_hashed_session(client, app, sessions):
    response = sign_in(client, "/modules?x=1")
    assert response.status_code == 302
    assert response.headers["location"] == "http://localhost:3000/modules?x=1"
    cookie = set_cookie(response, "mg_session")
    assert "HttpOnly" in cookie and "SameSite=lax" in cookie and "Path=/" in cookie
    assert f"Max-Age={30 * 86400}" in cookie and "Secure" not in cookie
    # The state cookie is discarded after use.
    assert 'mg_oauth_state=""' in set_cookie(response, oauth_state.STATE_COOKIE)
    token = client.cookies["mg_session"]
    with sessions() as session:
        user = session.scalar(select(User))
        stored = session.scalar(select(UserSession))
        assert (user.github_id, user.login, user.name) == (42, "octocat", "The Octocat")
        assert stored.user_id == user.id and stored.token_hash != token and len(token) >= 32
        # Cookie lifetime and server-side expiry agree.
        lifetime = stored.expires_at - stored.created_at
        assert lifetime == timedelta(days=30)
    me = client.get("/api/me")
    assert me.json() == {
        "user": {
            "login": "octocat",
            "name": "The Octocat",
            "avatar_url": "https://avatars.test/42",
        },
        "login_available": True,
    }
    assert me.headers["cache-control"] == "no-store"
    assert app.state.github.codes == ["good"]


def test_secure_cookie_follows_configured_https_address(auth_settings, sessions):
    settings = configured(
        auth_settings.model_copy(update={"api_public_url": "https://api.example.com"})
    )
    with TestClient(
        create_app(settings, sessions, oauth=FakeGitHub(settings)),
        base_url="https://api.example.com",
        follow_redirects=False,
    ) as client:
        response, query = start(client)
        assert "Secure" in set_cookie(response, oauth_state.STATE_COOKIE)
        assert query["redirect_uri"] == ["https://api.example.com/api/auth/github/callback"]
        done = client.get(
            "/api/auth/github/callback", params={"code": "good", "state": query["state"][0]}
        )
        assert "Secure" in set_cookie(done, "mg_session")


def test_forwarded_headers_cannot_enable_or_disable_secure(client):
    response = client.get("/api/auth/github/login", headers={"X-Forwarded-Proto": "https"})
    assert "Secure" not in set_cookie(response, oauth_state.STATE_COOKIE)


@pytest.mark.parametrize("case", ["missing", "mismatch", "tampered", "no_cookie", "expired"])
def test_callback_rejects_bad_state(client, sessions, case, monkeypatch):
    _, query = start(client)
    state = query["state"][0]
    params = {"code": "good", "state": state}
    if case == "missing":
        del params["state"]
    elif case == "mismatch":
        params["state"] = "x" * len(state)
    elif case == "tampered":
        value = client.cookies[oauth_state.STATE_COOKIE]
        client.cookies.set(
            oauth_state.STATE_COOKIE,
            value[:-1] + ("0" if value[-1] != "0" else "1"),
            path="/api/auth",
        )
    elif case == "no_cookie":
        client.cookies.clear()
    elif case == "expired":
        future = time.time() + oauth_state.STATE_TTL_SECONDS + 1
        monkeypatch.setattr("app.auth.state.time.time", lambda: future)
    response = client.get("/api/auth/github/callback", params=params)
    assert response.headers["location"] == "http://localhost:3000/home?login_error=state"
    assert not any(h.startswith("mg_session=") for h in response.headers.get_list("set-cookie"))
    with sessions() as session:
        assert session.scalar(select(func.count(User.id))) == 0


def test_state_is_single_use(client):
    _, query = start(client)
    params = {"code": "good", "state": query["state"][0]}
    assert (
        "login_error"
        not in client.get("/api/auth/github/callback", params=params).headers["location"]
    )
    # The browser dropped the state cookie, so replaying the same callback fails.
    assert oauth_state.STATE_COOKIE not in client.cookies
    replay = client.get("/api/auth/github/callback", params=params)
    assert replay.headers["location"].endswith("login_error=state")


def test_provider_failure_and_denial_do_not_sign_in(client, sessions):
    failed = sign_in(client, "/modules", code="bad")
    assert failed.headers["location"] == "http://localhost:3000/modules?login_error=github"
    _, query = start(client, "/modules")
    denied = client.get(
        "/api/auth/github/callback", params={"error": "access_denied", "state": query["state"][0]}
    )
    assert denied.headers["location"] == "http://localhost:3000/modules?login_error=denied"
    assert client.get("/api/me").json()["user"] is None
    with sessions() as session:
        assert session.scalar(select(func.count(UserSession.id))) == 0


@pytest.mark.parametrize(
    "target",
    [
        "//evil.com",
        "https://evil.com",
        "http://evil.com/dashboard",
        "/\\evil.com",
        "\\\\evil.com",
        "evil.com",
        "javascript:alert(1)",
        "/ok\r\nSet-Cookie: x=1",
        "/" + "a" * 600,
    ],
)
def test_open_redirect_targets_are_refused(client, target):
    assert oauth_state.safe_next(target) == "/home"
    assert sign_in(client, target).headers["location"] == "http://localhost:3000/home"


@pytest.mark.parametrize("target", ["/", "/modules", "/scans/abc?tab=2", "/@user/vpc"])
def test_internal_paths_are_kept(client, target):
    assert sign_in(client, target).headers["location"] == f"http://localhost:3000{target}"


def test_every_login_issues_a_new_session_and_revokes_the_replaced_one(client, sessions):
    sign_in(client)
    first = client.cookies["mg_session"]
    sign_in(client)
    second = client.cookies["mg_session"]
    assert first != second
    with sessions() as session:
        assert session.scalar(select(func.count(UserSession.id))) == 1
        assert session.scalar(select(func.count(User.id))) == 1
    replace_session_cookie(client, first)
    assert client.get("/api/me").json()["user"] is None


def test_attacker_planted_session_cookie_is_not_adopted(client):
    # Session fixation: a pre-set cookie value never becomes the authenticated token.
    replace_session_cookie(client, "attacker-chosen-token")
    sign_in(client)
    assert client.cookies["mg_session"] != "attacker-chosen-token"
    replace_session_cookie(client, "attacker-chosen-token")
    assert client.get("/api/me").json()["user"] is None


def test_profile_follows_github_id_across_renames(client, app, sessions):
    sign_in(client)
    app.state.github.profile = GitHubProfile(42, "renamed", None, None)
    sign_in(client)
    assert client.get("/api/me").json()["user"] == {
        "login": "renamed",
        "name": None,
        "avatar_url": None,
    }
    # Someone else now owns the old login, and a third account takes over "renamed".
    app.state.github.profile = GitHubProfile(7, "octocat", "New Owner", None)
    sign_in(client)
    app.state.github.profile = GitHubProfile(99, "renamed", "Squatter", None)
    sign_in(client)
    with sessions() as session:
        logins = dict(session.execute(select(User.github_id, User.login)).all())
    assert logins == {42: "gh-42", 7: "octocat", 99: "renamed"}


def test_expired_session_is_ignored(client, sessions):
    sign_in(client)
    with sessions() as session:
        session.scalar(select(UserSession)).expires_at = utcnow() - timedelta(seconds=1)
        session.commit()
    assert client.get("/api/me").json()["user"] is None
    # Expired rows are purged at the next login.
    sign_in(client)
    with sessions() as session:
        assert session.scalar(select(func.count(UserSession.id))) == 1


def test_logout_is_post_only_and_checks_origin(client, sessions):
    sign_in(client)
    for method in ("get", "delete", "put"):
        assert getattr(client, method)("/api/auth/logout").status_code == 405
    for headers in ({}, {"Origin": "https://evil.com"}, {"Origin": "null"}):
        refused = client.post("/api/auth/logout", headers=headers)
        assert refused.status_code == 403
        assert refused.json()["error"]["code"] == "ForbiddenOriginError"
    assert client.get("/api/me").json()["user"]["login"] == "octocat"
    token = client.cookies["mg_session"]
    done = client.post("/api/auth/logout", headers={"Origin": ORIGIN})
    assert done.status_code == 204
    assert 'mg_session=""' in set_cookie(done, "mg_session")
    # Revoked on the server: the old token is useless even if the cookie is replayed.
    replace_session_cookie(client, token)
    assert client.get("/api/me").json()["user"] is None
    with sessions() as session:
        assert session.scalar(select(func.count(UserSession.id))) == 0
    assert client.post("/api/auth/logout", headers={"Origin": ORIGIN}).status_code == 204


def test_cors_allows_credentials_only_for_listed_origins(client):
    allowed = client.get("/api/me", headers={"Origin": ORIGIN})
    assert allowed.headers["access-control-allow-origin"] == ORIGIN
    assert allowed.headers["access-control-allow-credentials"] == "true"
    denied = client.get("/api/me", headers={"Origin": "https://evil.com"})
    assert "access-control-allow-origin" not in denied.headers
    preflight = client.options(
        "/api/auth/logout",
        headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST"},
    )
    assert preflight.headers["access-control-allow-origin"] == ORIGIN
    blocked = client.options(
        "/api/auth/logout",
        headers={"Origin": "https://evil.com", "Access-Control-Request-Method": "POST"},
    )
    assert blocked.status_code == 400 and "access-control-allow-origin" not in blocked.headers


def test_unsafe_settings_refuse_to_start(settings):
    base = {**settings.model_dump(), "_env_file": None}
    with pytest.raises(ValidationError, match="explicit origins"):
        Settings(**{**base, "cors_origins": ["*"]})
    fake = "http://localhost:8000/api/dev/oauth/token"
    for key in ("github_authorize_url", "github_token_url", "github_user_url"):
        with pytest.raises(ValidationError, match="cannot be overridden in production"):
            Settings(**{**base, "app_environment": "production", key: fake})
        assert Settings(**{**base, "app_environment": "development", key: fake})


def test_fake_provider_exists_only_in_development(settings, sessions):
    base = {**settings.model_dump(), "_env_file": None}
    paths = ("/api/dev/oauth/authorize?redirect_uri=x&state=y", "/api/dev/oauth/user")
    production = Settings(**{**base, "app_environment": "production"})
    with TestClient(create_app(production, sessions)) as client:
        assert [client.get(p).status_code for p in paths] == [404, 404]
        assert client.post("/api/dev/oauth/token").status_code == 404
    development = Settings(**{**base, "app_environment": "development"})
    with TestClient(create_app(development, sessions), follow_redirects=False) as client:
        own = "http://localhost:8000/api/auth/github/callback"
        ok = client.get("/api/dev/oauth/authorize", params={"redirect_uri": own, "state": "s"})
        assert ok.headers["location"] == f"{own}?code=dev-code-dev-user&state=s"
        # Never a redirector to arbitrary sites, even in development.
        other = {"redirect_uri": "https://evil.com/cb", "state": "s"}
        assert client.get("/api/dev/oauth/authorize", params=other).status_code == 400


def test_full_flow_through_the_fake_provider(auth_settings, sessions, monkeypatch):
    """The real GitHubOAuth client against the development provider, as e2e runs it."""
    import httpx

    settings = Settings(
        **{
            **auth_settings.model_dump(),
            "_env_file": None,
            "app_environment": "development",
            "github_authorize_url": "http://localhost:8000/api/dev/oauth/authorize",
            "github_token_url": "http://localhost:8000/api/dev/oauth/token",
            "github_user_url": "http://localhost:8000/api/dev/oauth/user",
        }
    )
    application = create_app(settings, sessions)
    original = httpx.AsyncClient

    def in_process(**kwargs):
        return original(
            transport=httpx.ASGITransport(app=application),
            **{k: v for k, v in kwargs.items() if k != "transport"},
        )

    monkeypatch.setattr("app.auth.oauth.httpx.AsyncClient", in_process)
    with TestClient(
        application, base_url="http://localhost:8000", follow_redirects=False
    ) as client:
        response, _ = start(client, "/modules")
        approved = client.get(response.headers["location"])
        done = client.get(approved.headers["location"])
        assert done.headers["location"] == "http://localhost:3000/modules"
        assert client.get("/api/me").json()["user"]["login"] == "dev-user"


def test_login_unavailable_without_configuration(settings, sessions):
    with TestClient(create_app(settings, sessions), follow_redirects=False) as client:
        for path in ("/api/auth/github/login", "/api/auth/github/callback?code=x&state=y"):
            response = client.get(path)
            assert response.status_code == 503
            assert response.json()["error"]["code"] == "LoginUnavailableError"
        assert client.get("/api/me").json() == {"user": None, "login_available": False}
        assert client.get("/api/repositories").status_code == 200
        assert client.get("/health").status_code == 200
    # Compose passes unset variables as empty strings.
    blank = Settings(
        **{**settings.model_dump(), "_env_file": None, "github_client_id": "", "session_secret": ""}
    )
    assert not blank.auth_configured


def test_secrets_never_reach_responses_or_logs(client, caplog):
    caplog.set_level(logging.DEBUG)
    bodies = []
    for response in (
        start(client)[0],
        sign_in(client),
        sign_in(client, code="bad"),
        client.get("/api/me"),
        client.get("/openapi.json"),
    ):
        bodies.append(response.text + str(response.headers.get("location", "")))
    token = client.cookies["mg_session"]
    haystack = "\n".join(bodies) + caplog.text
    for secret in (*SECRETS.values(), token):
        assert secret not in haystack
    assert "client-secret-value" not in repr(client.app.state.settings)


def test_system_account_cannot_be_entered_or_renamed(client, app, sessions):
    from app.auth import sessions as auth_sessions
    from app.core.exceptions import SystemAccountError

    with sessions() as session:
        mirror = User(login="Some-Mirror", name="Mirror", is_system=True)
        session.add(mirror)
        session.commit()
        mirror_id = mirror.id
        with pytest.raises(SystemAccountError):
            auth_sessions.create(session, mirror, 3600)

    # GitHub accounts arriving under the system login (any case) or a reserved name.
    arrivals = [
        (501, "Some-Mirror"),
        (502, "some-mirror"),
        (503, "terraform-aws-modules"),
        (504, "Terraform-AWS-Modules-Mirror"),
        (505, "ADMIN"),
    ]
    for github_id, login in arrivals:
        app.state.github.profile = GitHubProfile(github_id, login, "Impostor", None)
        sign_in(client)
        assert client.get("/api/me").json()["user"]["login"] == f"gh-{github_id}"
    with sessions() as session:
        stored = session.get(User, mirror_id)
        assert (stored.login, stored.name, stored.github_id) == ("Some-Mirror", "Mirror", None)
        assert stored.is_system
        logins = set(session.scalars(select(User.login)))
        assert logins == {"Some-Mirror", *(f"gh-{i}" for i, _ in arrivals)}
        # Even a session row that somehow points at a system account opens nothing.
        session.add(
            UserSession(
                user_id=mirror_id,
                token_hash=auth_sessions._hash("forged"),
                created_at=utcnow(),
                expires_at=utcnow() + timedelta(hours=1),
            )
        )
        session.commit()
    client.cookies.clear()
    client.cookies.set(auth_sessions.SESSION_COOKIE, "forged")
    assert client.get("/api/me").json()["user"] is None
    assert client.get("/api/me/repositories").status_code == 401
    # Returning under an ordinary name works as before.
    app.state.github.profile = GitHubProfile(501, "ordinary", None, None)
    sign_in(client)
    assert client.get("/api/me").json()["user"]["login"] == "ordinary"
