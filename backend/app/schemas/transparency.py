from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class TreeHead(BaseModel):
    tree_size: int
    root_hash: str


class CheckpointInfo(TreeHead):
    created_at: datetime


class AnchorInfo(BaseModel):
    type: Literal["opentimestamps", "rfc3161"]
    status: Literal["submitted", "failed"]
    receipt_sha256: str | None = None
    created_at: datetime


class CheckpointResponse(BaseModel):
    schema_version: Literal[1] = 1
    enabled: bool = False
    checkpoint: CheckpointInfo | None = None
    anchors: list[AnchorInfo] = Field(default_factory=list)


class ConsistencyResponse(BaseModel):
    schema_version: Literal[1] = 1
    first: int
    second: int
    first_root: str
    second_root: str
    proof: list[str]


class Commitment(BaseModel):
    scan_id: str
    module_version_id: str | None
    status: Literal["COMPLETED", "FAILED"]
    result_sha256: str
    manifest_sha256: str | None
    snapshot_sha256: str | None


class LogEntry(BaseModel):
    """Exactly as stored: strings stay strings, because the leaf hash covers their bytes."""

    format: int
    seq: int
    kind: Literal["SCAN", "REVISION"]
    reason: str | None
    recorded_at: str
    commitment: Commitment


class ScanTransparencyResponse(BaseModel):
    schema_version: Literal[1] = 1
    scan_id: UUID
    version_id: UUID
    status: Literal["included", "pending", "mismatch", "not_logged"]
    reasons: list[str] = Field(default_factory=list)
    leaf_index: int | None = None
    # Withheld when it would name a version the viewer may not see (a fork's hidden original).
    entry: LogEntry | None = None
    leaf_hash: str | None = None
    current_commitment_sha256: str | None = None
    logged_commitment_sha256: str | None = None
    revisions: int = 0
    checkpoint: TreeHead | None = None
    inclusion_proof: list[str] = Field(default_factory=list)
