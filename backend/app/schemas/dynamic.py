"""Non-executing contract. This module cannot launch Terraform or a worker."""

from typing import Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

Status = Literal["success", "failed", "aborted", "timeout", "not_run", "unobservable"]
Digest = str


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class DynamicEnvironment(Strict):
    image_digest: str = Field(pattern=r"^sha256:[a-f0-9]{64}$")
    terraform_version: str = Field(max_length=80)
    provider_versions: dict[str, str] = Field(default_factory=dict, max_length=100)
    lockfile_digest: str | None = Field(default=None, pattern=r"^[a-f0-9]{64}$")
    test_suite_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mock_version: str | None = Field(default=None, max_length=80)
    policy_version: str = Field(max_length=80)
    credentials: Literal["none"] = "none"
    network: Literal["denied"] = "denied"


class DynamicEvidence(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    code: Literal[
        "FIXTURE_ONLY",
        "SIMULATED_RESOURCE",
        "SIMULATED_FAILURE",
        "TIME_BUDGET",
        "CANCELLED",
        "MISSING_OBSERVATION",
    ]
    redacted: Literal[True] = True


class DynamicStage(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    name: Literal["prepare", "isolation", "test", "observe", "cleanup"]
    status: Status
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    evidence_ids: list[str] = Field(default_factory=list, max_length=100)
    error_type: Literal["fixture_failure", "timeout", "cancelled", "missing_observation"] | None = (
        None
    )

    @model_validator(mode="after")
    def ordered(self):
        if self.status == "not_run" and (self.started_at or self.finished_at):
            raise ValueError("Unexecuted stages have no execution timestamps")
        if self.status != "not_run" and (self.started_at is None or self.finished_at is None):
            raise ValueError("Observed stages require both timestamps")
        if self.started_at and self.finished_at < self.started_at:
            raise ValueError("Stage timestamps are reversed")
        if self.status == "success" and self.error_type:
            raise ValueError("Success cannot carry an error")
        return self


class ObservedNode(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    resource_type: str = Field(pattern=r"^[a-z][a-z0-9_]{0,80}$")
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class ObservedEdge(Strict):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,80}$")
    source: str
    target: str
    relation: Literal["simulated_dependency", "observed_dependency"]
    evidence_ids: list[str] = Field(min_length=1, max_length=100)


class DynamicRun(Strict):
    schema_version: Literal[1] = 1
    run_id: UUID
    input_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_digest: str = Field(pattern=r"^[a-f0-9]{64}$")
    mode: Literal["fixture", "mock", "runtime"]
    execution_occurred: bool
    status: Status
    environment: DynamicEnvironment
    stages: list[DynamicStage] = Field(max_length=5000)
    nodes: list[ObservedNode] = Field(default_factory=list, max_length=1000)
    edges: list[ObservedEdge] = Field(default_factory=list, max_length=2000)
    evidence: list[DynamicEvidence] = Field(default_factory=list, max_length=5000)
    secret_policy: Literal["structured-allowlist-v1"] = "structured-allowlist-v1"

    @model_validator(mode="after")
    def consistent(self):
        if self.mode == "fixture" and self.execution_occurred:
            raise ValueError("Fixtures never execute")
        if self.mode != "fixture" and not self.execution_occurred and self.status != "not_run":
            raise ValueError("Missing execution cannot imply observed results")
        for rows in (self.nodes, self.edges, self.stages, self.evidence):
            if len({row.id for row in rows}) != len(rows):
                raise ValueError("Duplicate identifiers")
        evidence_ids = {e.id for e in self.evidence}
        node_ids = {n.id for n in self.nodes}
        for row in [*self.stages, *self.nodes, *self.edges]:
            if not set(row.evidence_ids) <= evidence_ids:
                raise ValueError("Missing evidence")
        for edge in self.edges:
            if edge.source not in node_ids or edge.target not in node_ids:
                raise ValueError("Missing endpoint")
            if self.mode != "runtime" and edge.relation == "observed_dependency":
                raise ValueError("Simulations cannot assert runtime edges")
        if self.status == "not_run" and (
            self.nodes or self.edges or any(s.status != "not_run" for s in self.stages)
        ):
            raise ValueError("Unexecuted runs contain no observations")
        if self.status == "success" and any(s.status != "success" for s in self.stages):
            raise ValueError("Incomplete stages cannot become success")
        return self
