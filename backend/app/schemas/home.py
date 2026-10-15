from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from app.schemas.scan import FindingSummary
from app.schemas.social import RepositoryCard


class Attention(BaseModel):
    name: str
    # failed: the latest attempt was not published. stale: the newest scan is old.
    reason: Literal["failed", "stale"]
    since: datetime | None


class Activity(BaseModel):
    name: str
    version: str | None
    # published / failed / running: a version's scan. forked: copied from another module.
    kind: Literal["published", "failed", "running", "forked"]
    at: datetime


class HomeResponse(BaseModel):
    repositories: list[RepositoryCard]
    attention: list[Attention]
    activity: list[Activity]
    # Module-body findings of each repository's newest stored version, added up.
    severity: FindingSummary
    stale_after_days: int
