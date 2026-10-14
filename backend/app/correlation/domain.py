from dataclasses import dataclass, field
from enum import StrEnum

from pydantic import BaseModel, Field

from app.parsers.domain import TerraformEntity
from app.schemas.finding import NormalizedFinding
from app.schemas.flow import FlowEdgeResponse


class Confidence(StrEnum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class PathQuality(StrEnum):
    RESOLVED = "RESOLVED"
    OPAQUE = "OPAQUE"
    PARTIAL = "PARTIAL"
    UNRESOLVED = "UNRESOLVED"


@dataclass
class FindingMatch:
    finding: NormalizedFinding
    entity: TerraformEntity
    confidence: Confidence
    method: str
    attributes: tuple[str, ...] = ()


@dataclass
class FlowCandidate:
    path: list[FlowEdgeResponse]
    source: dict
    sink: dict
    quality: PathQuality
    trust: list[NormalizedFinding]
    checkov: list[FindingMatch]

    @property
    def mutable(self) -> bool:
        return any(f.rule_id in {"TFSC003", "TFSC004"} for f in self.trust)

    @property
    def sink_category(self) -> str:
        return self.sink.get("sink_category", "UNKNOWN")


class CorrelationSummary(BaseModel):
    rules_evaluated: int = 0
    findings: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    paths_evaluated: int = 0
    traversal_steps: int = 0
    suppressed: int = 0
    truncated: bool = False
    duration_ms: int = 0
    rule_errors: list[str] = Field(default_factory=list)


@dataclass
class CorrelationResult:
    findings: list[NormalizedFinding] = field(default_factory=list)
    summary: CorrelationSummary = field(default_factory=CorrelationSummary)
    diagnostics: list[dict] = field(default_factory=list)
