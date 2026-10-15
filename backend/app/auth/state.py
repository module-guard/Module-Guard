"""OAuth state bound to a short-lived signed cookie (CSRF protection for the callback)."""

import base64
import hashlib
import hmac
import secrets
import time

STATE_COOKIE = "mg_oauth_state"
STATE_TTL_SECONDS = 600
DEFAULT_NEXT = "/home"


def safe_next(value: str | None) -> str:
    """Only same-site paths are accepted; anything else falls back to the default page."""
    if (
        not value
        or len(value) > 512
        or not value.startswith("/")
        or value.startswith("//")
        or "\\" in value
        or any(ord(character) < 0x20 or ord(character) == 0x7F for character in value)
    ):
        return DEFAULT_NEXT
    return value


def _sign(secret: str, payload: str) -> str:
    return hmac.new(secret.encode(), payload.encode(), hashlib.sha256).hexdigest()


def issue(secret: str, next_path: str, now: float | None = None) -> tuple[str, str]:
    """Returns (state sent to the provider, cookie value that binds it to this browser)."""
    state = secrets.token_urlsafe(32)
    expires = int((now or time.time()) + STATE_TTL_SECONDS)
    target = base64.urlsafe_b64encode(next_path.encode()).decode()
    payload = f"{state}.{expires}.{target}"
    return state, f"{payload}.{_sign(secret, payload)}"


def verify(
    secret: str, cookie: str | None, state: str | None, now: float | None = None
) -> str | None:
    """Returns the stored next path when cookie and state match and are fresh, else None."""
    if not cookie or not state:
        return None
    payload, _, signature = cookie.rpartition(".")
    if not hmac.compare_digest(signature, _sign(secret, payload)):
        return None
    try:
        expected, expires, target = payload.split(".")
        if int(expires) < (now or time.time()):
            return None
        next_path = base64.urlsafe_b64decode(target.encode()).decode()
    except (ValueError, UnicodeDecodeError):
        return None
    if not hmac.compare_digest(expected, state):
        return None
    return safe_next(next_path)
