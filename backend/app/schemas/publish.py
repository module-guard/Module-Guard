from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import OriginType, Visibility
from app.schemas.scan import LatestScanSummary
from app.schemas.social import RepositoryRef


class GitHubImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=1, max_length=2048)


class RegistryImportRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    # namespace/name/provider, e.g. "terraform-aws-modules/vpc/aws". The precise format and
    # the version string are validated by the collector so the error shape stays consistent.
    address: str = Field(min_length=1, max_length=256)
    version: str | None = Field(default=None, max_length=64)


class ExcludedFile(BaseModel):
    path: str
    reason: str


class SecretWarning(BaseModel):
    file: str
    line: int
    rule: str
    title: str
    # First four characters of the suspected value, the rest masked.
    preview: str | None


class UploadReport(BaseModel):
    """What was checked and will be stored if this prepared source is registered."""

    upload_id: str
    origin_type: Literal["GITHUB", "UPLOAD"]
    origin_url: str | None
    commit_hash: str | None
    suggested_name: str
    # SPDX id of the LICENSE that will be stored; None when missing or not recognised.
    license: str | None = None
    file_count: int
    size_bytes: int
    excluded: list[ExcludedFile]
    excluded_count: int
    # Entropy-based findings the user must confirm are not secrets before registering.
    warnings: list[SecretWarning]
    expires_at: datetime


class RepositoryCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(min_length=16, max_length=128)
    name: str = Field(pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    description: str | None = Field(default=None, max_length=500)
    visibility: Visibility = Visibility.PUBLIC
    # Required when the prepared source has entropy warnings.
    acknowledge_warnings: bool = False


class RepositoryCreated(BaseModel):
    owner: str
    name: str
    version: str
    visibility: Visibility
    scan_id: UUID


class MyRepository(BaseModel):
    name: str
    description: str | None
    visibility: Visibility
    origin_type: OriginType
    origin_url: str | None
    created_at: datetime
    version: str | None
    latest_scan: LatestScanSummary | None
    # PUBLISHED: has a stored version. PENDING: first scan still running. FAILED: the first
    # scan failed, so nothing is listed anywhere; the owner can retry or delete it.
    state: Literal["PUBLISHED", "PENDING", "FAILED"]
    failure: str | None


class VersionCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")
    upload_id: str = Field(min_length=16, max_length=128)
    acknowledge_warnings: bool = False


class VersionSummary(BaseModel):
    version: str | None
    created_at: datetime
    commit_hash: str | None
    # STORED: usable. PENDING / FAILED attempts are returned to the owner only.
    state: Literal["STORED", "PENDING", "FAILED"]
    failure: str | None
    scan: LatestScanSummary | None
    scanner_breakdown: dict[str, int] | None
    # Distinct rules (not occurrences) at each level: what the security badge counts.
    critical_rules: int
    high_rules: int


class VersionDetail(VersionSummary):
    readme: str | None
    variables: list[dict[str, Any]] | None
    outputs: list[dict[str, Any]] | None
    license: str | None
    # The tag that was fetched, when the source was pinned to one.
    source_ref: str | None = None
    file_count: int | None
    size_bytes: int | None


class RepositoryDetail(BaseModel):
    owner: str
    # Owned by a system account: a copy of the origin repository kept by the service.
    mirror: bool = False
    owner_name: str | None
    owner_avatar_url: str | None
    name: str
    description: str | None
    provider: str | None
    visibility: Visibility
    origin_type: OriginType
    origin_url: str | None
    star_count: int
    fork_count: int
    created_at: datetime
    updated_at: datetime
    is_owner: bool
    # Whether the viewer has starred it, and the origin of a fork when the viewer may see it.
    starred: bool
    forked_from: RepositoryRef | None
    latest_version: str | None
    # The selected version (latest stored one unless ?version= is given).
    version: VersionDetail | None
    versions: list[VersionSummary]


class RepositoryEdit(BaseModel):
    """Only the fields that are sent change."""

    model_config = ConfigDict(extra="forbid")
    name: str | None = Field(default=None, pattern=r"^[a-z0-9][a-z0-9_-]{0,99}$")
    description: str | None = Field(default=None, max_length=500)
    visibility: Visibility | None = None
