"""Additive trust exploration contracts. No raw expressions or secret values."""

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class EvidenceLocation(BaseModel):
    file: str | None = None
    line: int | None = None
    address: str | None = None
    severity: str | None = None
    rule: str | None = None


class Change(BaseModel):
    key: str
    kind: Literal["resource", "edge", "finding"]
    change: Literal["added", "removed", "modified", "unchanged", "uncertain"]
    before: EvidenceLocation | None = None
    after: EvidenceLocation | None = None


class VersionComparison(BaseModel):
    before_version: UUID
    after_version: UUID
    before_scan: UUID | None = None
    after_scan: UUID | None = None
    comparable: bool = False
    tool_conditions_equal: bool = False
    manifest_equal: bool | None = None
    reasons: list[str] = Field(default_factory=list)
    changes: list[Change] = Field(default_factory=list)
    total: int = 0
    next_offset: int | None = None
    counts: dict[str, int] = Field(default_factory=dict)
    interpretation: str = "정적 분석 비교이며 이름·주소 이동의 동일성은 확인할 수 없습니다."


class TrustEvent(BaseModel):
    id: str
    stage: str
    at: datetime
    status: str
    digest: str | None = None
    actor: str | None = None
    evidence: str | None = None
    time_basis: str = "server_record"


class TrustTimeline(BaseModel):
    version_id: UUID
    scan_id: UUID | None = None
    events: list[TrustEvent] = Field(default_factory=list)
    unrecorded: list[str] = Field(default_factory=list)
    signature_status: str = "unknown"
    signature_reasons: list[str] = Field(default_factory=list)
    observed_at: datetime
    security_guarantee: Literal[False] = False


class ReuseEvidence(BaseModel):
    id: UUID
    target_scan_id: UUID
    source_scan_id: UUID | None = None
    reused: bool = False
    reason: str
    policy_version: str
    key: str | None = None
    created_at: datetime
    source_finished_at: datetime | None = None
    source_duration_ms: int | None = None
    lookup_duration_ms: int = 0
    current_source_status: str = "unknown"
    measured_saving_ms: int | None = None


class ReuseHistory(BaseModel):
    records: list[ReuseEvidence] = Field(default_factory=list)
    next_offset: int | None = None
    metric_note: str = (
        "측정 기록 없음: 원검사 소요 시간은 재실행했을 때의 절감 시간 측정값이 아닙니다."
    )
