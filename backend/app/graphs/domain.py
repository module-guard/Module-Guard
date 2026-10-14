from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field

from app.parsers.domain import TerraformEntity


class EdgeType(StrEnum):
    FLOWS_TO = "FLOWS_TO"
    DERIVED_FROM = "DERIVED_FROM"
    PASSES_TO_MODULE_INPUT = "PASSES_TO_MODULE_INPUT"
    RETURNS_FROM_MODULE_OUTPUT = "RETURNS_FROM_MODULE_OUTPUT"
    PROPAGATES_THROUGH_LOCAL = "PROPAGATES_THROUGH_LOCAL"
    SINKS_TO_RESOURCE_ATTRIBUTE = "SINKS_TO_RESOURCE_ATTRIBUTE"
    PASSES_TO_OUTPUT = "PASSES_TO_OUTPUT"
    CALLS_MODULE = "CALLS_MODULE"
    USES_PROVIDER = "USES_PROVIDER"
    READS_VARIABLE = "READS_VARIABLE"
    REFERENCES = "REFERENCES"
    READS_LOCAL = "READS_LOCAL"
    READS_DATA = "READS_DATA"
    CONSUMES_MODULE_OUTPUT = "CONSUMES_MODULE_OUTPUT"
    RESOURCE_DEPENDS_ON = "RESOURCE_DEPENDS_ON"
    EXPLICIT_DEPENDS_ON = "EXPLICIT_DEPENDS_ON"


class Reference(BaseModel):
    source_address: str
    target_address: str
    reference_type: EdgeType
    expression: str
    attribute_path: str
    traversal: str
    target_attribute: str | None = None


class DependencyEdge(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source_entity_id: UUID | None
    target_entity_id: UUID | None = None
    target_address: str
    # Open string deliberately allows later data-flow edge types without DB enum migration.
    edge_type: str
    expression: str | None = None
    attribute_name: str | None = None
    metadata: dict[str, Any] = Field(default_factory=dict)


class DependencyGraph(BaseModel):
    nodes: list[TerraformEntity] = Field(default_factory=list)
    edges: list[DependencyEdge] = Field(default_factory=list)
    diagnostics: list[str] = Field(default_factory=list)
    references: list[Reference] = Field(default_factory=list)
    schema_version: int = 1
