import re
from urllib.parse import urlsplit

from app.collectors.base import SourceIdentity
from app.core.exceptions import UnsupportedSourceError
from app.models.enums import SourceType

_GITHUB_PATH = re.compile(r"/([a-zA-Z0-9][a-zA-Z0-9-]{0,38})/([a-zA-Z0-9_.-]{1,100})/?")
_REGISTRY = re.compile(
    r"([a-zA-Z0-9][a-zA-Z0-9_-]*)/([a-zA-Z0-9][a-zA-Z0-9_-]*)/([a-zA-Z0-9][a-zA-Z0-9_-]*)"
)


def normalize_github_url(source: str) -> str:
    source = source.strip()
    if any(ord(c) < 32 or ord(c) == 127 for c in source):
        raise UnsupportedSourceError("Invalid source URL.")
    try:
        parsed = urlsplit(source)
        match = _GITHUB_PATH.fullmatch(parsed.path)
        if (
            parsed.scheme != "https"
            or parsed.netloc.lower() != "github.com"
            or parsed.query
            or parsed.fragment
            or "?" in source
            or "#" in source
            or not match
        ):
            raise ValueError
        owner, repo = match.groups()
        repo = repo.removesuffix(".git")
        if repo in {"", ".", ".."} or repo.startswith("-"):
            raise ValueError
    except ValueError:
        raise UnsupportedSourceError(
            "Use https://github.com/owner/repository or namespace/name/provider."
        ) from None
    return f"https://github.com/{owner.lower()}/{repo.lower()}"


def detect_source(source: str) -> SourceIdentity:
    source = source.strip()
    registry = _REGISTRY.fullmatch(source)
    if registry:
        owner, name, provider = registry.groups()
        return SourceIdentity(SourceType.TERRAFORM_REGISTRY, source, owner, name, provider)
    normalized = normalize_github_url(source)
    owner, name = normalized.removeprefix("https://github.com/").split("/")
    return SourceIdentity(SourceType.GITHUB, normalized, owner, name)
