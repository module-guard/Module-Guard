from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OriginType, Visibility


class RepositoryRef(BaseModel):
    owner: str
    name: str


class BadgeInfo(BaseModel):
    """What a card needs to draw the security badge for the newest stored version."""

    version: str | None
    critical_rules: int
    high_rules: int
    unknown: int
    total: int
    scanned_at: datetime | None


class RepositoryCard(BaseModel):
    owner: str
    mirror: bool = False
    owner_avatar_url: str | None
    name: str
    description: str | None
    provider: str | None
    visibility: Visibility
    origin_type: OriginType
    # The origin of a fork, when the viewer may see it.
    forked_from: RepositoryRef | None
    star_count: int
    fork_count: int
    updated_at: datetime
    # PENDING and FAILED repositories are only ever returned to their owner.
    state: Literal["PUBLISHED", "PENDING", "FAILED"]
    failure: str | None
    badge: BadgeInfo | None


class UserProfile(BaseModel):
    login: str
    # A system account that holds mirrors; nobody signs in to it.
    mirror: bool = False
    name: str | None
    avatar_url: str | None
    is_self: bool
    repository_count: int
    fork_count: int
    star_count: int


class StarState(BaseModel):
    starred: bool
    star_count: int


class ForkRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # Omitted: the origin's name, or name-1, name-2… when taken.
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    visibility: Visibility = Visibility.PRIVATE


class ForkCreated(BaseModel):
    owner: str
    name: str
    visibility: Visibility
    forked_from: RepositoryRef
