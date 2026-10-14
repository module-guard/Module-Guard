from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class FlowNode(BaseModel):
    id: str
    address: str
    entity_id: str | None = None
    entity_address: str | None = None
    entity_type: str = "UNRESOLVED"
    attribute_path: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowEdgeResponse(BaseModel):
    id: UUID
    source: str
    target: str
    type: str
    expression: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class FlowResponse(BaseModel):
    nodes: list[FlowNode]
    edges: list[FlowEdgeResponse]
    # Counts cover all matching edges, before pagination.
    summary: dict[str, int]
    limit: int
    offset: int


class EntityFlowResponse(BaseModel):
    entity: str
    upstream: list[FlowEdgeResponse]
    downstream: list[FlowEdgeResponse]
    truncated: bool = False


class FlowTraceResponse(BaseModel):
    found: bool
    paths: list[list[FlowNode]]
    path_edge_ids: list[list[UUID]]
    truncated: bool = False
    explored_steps: int = 0
    max_depth: int
