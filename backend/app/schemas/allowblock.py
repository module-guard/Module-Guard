from datetime import datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import AfterValidator, AwareDatetime, BaseModel, ConfigDict, Field

from app.schemas.social import RepositoryRef

TargetKind = Literal["repository", "github_repository", "github_owner"]
Effect = Literal["ALLOW", "BLOCK"]
Status = Literal["ACTIVE", "REVOKED", "EXPIRED"]


def _http_urls(urls: list[str]) -> list[str]:
    if any(not u.startswith(("https://", "http://")) or len(u) > 2048 for u in urls):
        raise ValueError("Evidence must be http(s) URLs.")
    return urls


Evidence = Annotated[list[str], Field(max_length=10), AfterValidator(_http_urls)]
Reason = Annotated[str, Field(min_length=1, max_length=2000)]


class EntryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_kind: TargetKind
    target: Annotated[str, Field(min_length=1, max_length=255)]
    effect: Effect
    reason: Reason
    evidence_urls: Evidence = Field(default_factory=list)
    public_message: Annotated[str, Field(max_length=500)] | None = None
    expires_at: AwareDatetime | None = None


class EntryRevoke(BaseModel):
    model_config = ConfigDict(extra="forbid")
    reason: Reason
    evidence_urls: Evidence = Field(default_factory=list)


class AdminEntry(BaseModel):
    id: UUID
    target_kind: TargetKind
    target_key: str
    repository_id: UUID | None
    effect: Effect
    status: Status
    reason: str
    evidence_urls: list[str]
    public_message: str | None
    created_by_login: str | None
    created_at: datetime
    expires_at: datetime | None
    revoked_at: datetime | None
    revoked_by_login: str | None
    revoke_reason: str | None
    revoke_evidence_urls: list[str] | None


class AdminEntryPage(BaseModel):
    entries: list[AdminEntry]
    next_offset: int | None


class AdminEvent(BaseModel):
    id: UUID
    action: Literal["CREATE", "REVOKE", "EXPIRE"]
    actor_login: str | None
    occurred_at: datetime
    reason: str
    evidence_urls: list[str]
    snapshot: dict


class EventPage(BaseModel):
    events: list[AdminEvent]
    next_offset: int | None


class PublicEntry(BaseModel):
    """What anyone may see: never the internal reason, evidence, actor or history."""

    repository: RepositoryRef | None = None
    effect: Effect
    status: Literal["ACTIVE"] = "ACTIVE"
    message: str
    expires_at: datetime | None


class PublicEntryPage(BaseModel):
    entries: list[PublicEntry]
    next_offset: int | None


class RepositoryPolicy(BaseModel):
    repository: RepositoryRef
    effect: Literal["ALLOW", "BLOCK", "NONE"]
    entries: list[PublicEntry]
