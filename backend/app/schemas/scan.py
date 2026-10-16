from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import ScanStatus, Visibility
from app.schemas.analysis import AnalysisSummary
from app.schemas.module import ModuleResponse


class ScanRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    source: str = Field(min_length=1, max_length=2048)


class ScanAccepted(BaseModel):
    scan_id: UUID
    status: ScanStatus = ScanStatus.QUEUED


class FindingSummary(BaseModel):
    total: int = 0
    critical: int = 0
    high: int = 0
    medium: int = 0
    low: int = 0
    info: int = 0
    unknown: int = 0


class ScopeSummary(BaseModel):
    """Findings outside the module body (examples, tests), and per-scanner counts by scope."""

    example: FindingSummary = Field(default_factory=FindingSummary)
    module_by_scanner: dict[str, int] = Field(default_factory=dict)
    example_by_scanner: dict[str, int] = Field(default_factory=dict)


class RepositoryRef(BaseModel):
    """The repository version a scan belongs to, when it was published by a user."""

    owner: str
    name: str
    version: str | None
    visibility: Visibility


class ScanResponse(BaseModel):
    id: UUID
    module_id: UUID
    status: ScanStatus
    scanner: str
    commit_hash: str | None
    branch: str | None
    module: ModuleResponse
    summary: FindingSummary
    partial_result: bool
    warnings: list[str]
    error_message: str | None
    started_at: datetime | None
    finished_at: datetime | None
    duration_ms: int | None
    created_at: datetime
    analysis: AnalysisSummary | None = None
    repository: RepositoryRef | None = None
    # summary counts the module body only; this holds the rest. None for scans made before
    # findings were classified.
    scope_summary: ScopeSummary | None = None
    # Set only on FAILED scans: which pipeline stage failed and the exception class name.
    failed_stage: str | None = None
    error_code: str | None = None

    @staticmethod
    def summarize(scan) -> FindingSummary:
        return FindingSummary(
            total=scan.total_findings,
            **{
                key: getattr(scan, f"{key}_count")
                for key in ("critical", "high", "medium", "low", "info", "unknown")
            },
        )

    @classmethod
    def from_scan(cls, scan):
        # "failure" rides alongside the analysis stages in the JSONB column; it is not part of
        # the AnalysisSummary schema, so it is read from the raw dict here.
        failure = (scan.analysis or {}).get("failure") or {}
        return cls(
            id=scan.id,
            analysis=scan.analysis,
            failed_stage=failure.get("stage"),
            error_code=failure.get("code"),
            module_id=scan.module_id,
            status=scan.status,
            scanner=scan.scanner,
            commit_hash=scan.commit_hash,
            branch=scan.branch,
            module=ModuleResponse.model_validate(scan.module),
            summary=cls.summarize(scan),
            partial_result=scan.partial_result,
            warnings=scan.warnings,
            error_message=scan.error_message,
            started_at=scan.started_at,
            finished_at=scan.finished_at,
            duration_ms=scan.duration_ms,
            created_at=scan.created_at,
            repository=cls.repository_of(scan),
            scope_summary=scan.scope_summary,
        )

    @staticmethod
    def repository_of(scan) -> RepositoryRef | None:
        version = scan.module_version
        if version is None or version.repository is None:
            return None
        return RepositoryRef(
            owner=version.repository.owner.login,
            name=version.repository.name,
            version=version.version,
            visibility=version.repository.visibility,
        )


class LatestScanSummary(BaseModel):
    """Compact latest-scan view for module lists; omits analysis and module payloads."""

    id: UUID
    status: ScanStatus
    summary: FindingSummary
    partial_result: bool
    created_at: datetime
    finished_at: datetime | None
    scope_summary: ScopeSummary | None = None

    @classmethod
    def from_scan(cls, scan):
        return cls(
            id=scan.id,
            status=scan.status,
            scope_summary=scan.scope_summary,
            summary=ScanResponse.summarize(scan),
            partial_result=scan.partial_result,
            created_at=scan.created_at,
            finished_at=scan.finished_at,
        )
