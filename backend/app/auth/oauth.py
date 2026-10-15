"""GitHub OAuth web flow: exchange the code, read the profile, keep nothing else."""

from dataclasses import dataclass
from urllib.parse import urlencode

import httpx

from app.core.config import Settings


class OAuthError(Exception):
    """Carries no provider text: responses may include tokens or attacker-chosen content."""


@dataclass(frozen=True)
class GitHubProfile:
    github_id: int
    login: str
    name: str | None
    avatar_url: str | None


class GitHubOAuth:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def redirect_uri(self) -> str:
        return f"{self.settings.api_public_url}/api/auth/github/callback"

    def authorize_url(self, state: str) -> str:
        query = urlencode(
            {
                "client_id": self.settings.github_client_id,
                "redirect_uri": self.redirect_uri,
                "scope": "read:user",
                "state": state,
            }
        )
        return f"{self.settings.github_authorize_url}?{query}"

    async def fetch_profile(self, code: str) -> GitHubProfile:
        """The access token lives only inside this call; it is never stored or logged."""
        try:
            async with httpx.AsyncClient(timeout=10, follow_redirects=False) as client:
                token_response = await client.post(
                    self.settings.github_token_url,
                    headers={"Accept": "application/json"},
                    data={
                        "client_id": self.settings.github_client_id,
                        "client_secret": self.settings.github_client_secret.get_secret_value(),
                        "code": code,
                        "redirect_uri": self.redirect_uri,
                    },
                )
                token = token_response.json().get("access_token")
                if token_response.status_code != 200 or not isinstance(token, str):
                    raise OAuthError("token")
                user_response = await client.get(
                    self.settings.github_user_url,
                    headers={
                        "Accept": "application/vnd.github+json",
                        "Authorization": f"Bearer {token}",
                    },
                )
                if user_response.status_code != 200:
                    raise OAuthError("profile")
                data = user_response.json()
            return GitHubProfile(
                github_id=int(data["id"]),
                login=str(data["login"])[:64],
                name=(str(data["name"])[:255] if data.get("name") else None),
                avatar_url=(str(data["avatar_url"])[:2048] if data.get("avatar_url") else None),
            )
        except OAuthError:
            raise
        except (httpx.HTTPError, ValueError, KeyError, TypeError, AttributeError):
            raise OAuthError("provider") from None
