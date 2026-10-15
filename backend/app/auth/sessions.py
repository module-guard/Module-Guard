"""Server-side sessions: the browser holds a random token, the database only its hash."""

import hashlib
import secrets
from collections.abc import Iterable
from datetime import timedelta

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app.auth.oauth import GitHubProfile
from app.core.exceptions import SystemAccountError
from app.models import User, UserSession
from app.models.common import utcnow

SESSION_COOKIE = "mg_session"


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _is_reserved(session: Session, login: str, reserved: Iterable[str]) -> bool:
    lowered = login.lower()
    if lowered in {name.lower() for name in reserved}:
        return True
    return (
        session.scalar(
            select(User.id).where(User.is_system.is_(True), func.lower(User.login) == lowered)
        )
        is not None
    )


def upsert_user(session: Session, profile: GitHubProfile, reserved: Iterable[str] = ()) -> User:
    """Users are identified by github_id; login, name and avatar follow GitHub on every login.

    A system account has no github_id, so no GitHub identity resolves to it. Its login, and
    every reserved one, is never handed out: whoever signs in under such a name is known as
    gh-<github_id> instead, and the system account is left exactly as it was.
    """
    user = session.scalar(select(User).where(User.github_id == profile.github_id))
    login = profile.login
    if _is_reserved(session, login, reserved):
        login = f"gh-{profile.github_id}"
    # A login freed by a rename can be taken by another account: release the stale holder.
    holder = session.scalar(select(User).where(User.login == login))
    if holder is not None and holder is not user:
        if holder.is_system:  # Unreachable while system logins are reserved; never rename one.
            raise SystemAccountError("System accounts cannot be signed in to or renamed.")
        holder.login = f"gh-{holder.github_id}"
        session.flush()
    if user is None:
        user = User(github_id=profile.github_id, login=login)
        session.add(user)
    user.login = login
    user.name = profile.name
    user.avatar_url = profile.avatar_url
    session.flush()
    return user


def create(session: Session, user: User, ttl_seconds: int, replaces: str | None = None) -> str:
    """Always issues a fresh token; the session of the cookie being replaced is revoked."""
    if user.is_system:
        raise SystemAccountError("System accounts cannot be signed in to or renamed.")
    now = utcnow()
    session.execute(delete(UserSession).where(UserSession.expires_at < now))
    if replaces:
        revoke(session, replaces)
    token = secrets.token_urlsafe(32)
    session.add(
        UserSession(
            user_id=user.id,
            token_hash=_hash(token),
            created_at=now,
            expires_at=now + timedelta(seconds=ttl_seconds),
        )
    )
    session.flush()
    return token


def find_user(session: Session, token: str | None) -> User | None:
    if not token:
        return None
    return session.scalar(
        select(User)
        .join(UserSession, UserSession.user_id == User.id)
        .where(
            UserSession.token_hash == _hash(token),
            UserSession.expires_at > utcnow(),
            User.is_system.is_(False),  # A session row for a system account opens nothing.
        )
    )


def revoke(session: Session, token: str | None) -> None:
    if token:
        session.execute(delete(UserSession).where(UserSession.token_hash == _hash(token)))
