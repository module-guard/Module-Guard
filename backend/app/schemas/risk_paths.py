from uuid import UUID

from pydantic import BaseModel, Field

from app.schemas.trust import EvidenceLocation


class RiskNode(BaseModel):
    id: str
    address: str
    type: str
    file_path: str | None = None
    line_start: int | None = None


class RiskEdge(BaseModel):
    id: str
    source: str
    target: str | None = None
    type: str
    basis: str = "static_adjacent"
    resolved: bool = False


class RiskPath(BaseModel):
    finding_id: UUID
    rule: str
    severity: str
    location: EvidenceLocation
    recommendation: str | None = None
    nodes: list[RiskNode] = Field(default_factory=list)
    edges: list[RiskEdge] = Field(default_factory=list)
    truncated: bool = False
    interpretation: str = "정적 추론 관계이며 실제 통신·실행 관측이 아닙니다. 인접 연결만으로 위험 전파를 확정하지 않습니다."


class RiskPaths(BaseModel):
    scan_id: UUID
    incomplete: bool = True
    paths: list[RiskPath] = Field(default_factory=list)
    next_offset: int | None = None
