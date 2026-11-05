from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

EvidenceStatus = Literal[
    "confirmed", "insufficient", "unverified", "failed", "not_applicable", "stale"
]


class ReputationMetric(BaseModel):
    id: str
    axis: Literal["security", "maintenance", "provenance", "documentation", "tests", "popularity"]
    status: EvidenceStatus
    value: str | int | bool | None = None
    source_url: str | None = None
    formula: str
    reason: str | None = None


class ReputationEvaluation(BaseModel):
    version_id: UUID
    collected_at: datetime
    policy_version: str = "evidence-v1"
    metrics: list[ReputationMetric] = Field(default_factory=list)
    coverage: dict[str, float] = Field(default_factory=dict)
    raw: dict = Field(default_factory=dict)
    total_score: None = None
    validation: Literal["not_independently_validated"] = "not_independently_validated"
    notice: str = "근거 충족률은 보안 점수가 아닙니다. 독립 평가 전 총점을 제공하지 않습니다."


class ReputationHistory(BaseModel):
    entries: list[ReputationEvaluation] = Field(default_factory=list)
    next_offset: int | None = None
