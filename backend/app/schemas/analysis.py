from enum import StrEnum
from typing import Any

from pydantic import BaseModel, Field

from app.correlation.domain import CorrelationSummary
from app.graphs.flow_domain import FlowSummary


class StageStatus(StrEnum):
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    PARTIAL = "PARTIAL"
    FAILED = "FAILED"
    SKIPPED = "SKIPPED"


class AnalysisStage(BaseModel):
    status: StageStatus = StageStatus.PENDING
    error_code: str | None = None
    message: str | None = None


class AnalysisSummary(BaseModel):
    scanner_breakdown: dict[str, int] | None = None
    schema_version: int = 1
    stages: dict[str, AnalysisStage] = Field(default_factory=dict)
    entities: int = 0
    edges: int = 0
    dependency_edges: int = 0
    data_flow: FlowSummary | None = None
    correlation: CorrelationSummary | None = None
    modules: int = 0
    external_modules: int = 0
    providers: int = 0
    source_trust_findings: int = 0
    unresolved_references: int = 0
    files_parsed: int = 0
    files_failed: int = 0
    max_observed_module_depth: int = 0
    external_transitive_depth_unknown: bool = False
    diagnostics: list[dict[str, Any]] = Field(default_factory=list)
