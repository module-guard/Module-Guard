from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import AliasChoices, BaseModel, ConfigDict, Field

from app.parsers.domain import EntityType
from app.schemas.analysis import AnalysisSummary


class EntityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scan_id: UUID
    entity_type: EntityType
    address: str
    name: str
    resource_type: str | None
    file_path: str
    line_start: int | None
    line_end: int | None
    attributes: dict[str, Any]
    metadata: dict[str, Any] = Field(validation_alias=AliasChoices("entity_metadata", "metadata"))
    created_at: datetime


class GraphNodeResponse(BaseModel):
    id: str
    address: str
    type: str
    metadata: dict[str, Any]
    file_path: str | None = None
    line_start: int | None = None
    line_end: int | None = None


class GraphEdgeResponse(BaseModel):
    id: UUID
    source: str
    target: str | None
    target_address: str | None
    type: str
    expression: str | None
    attribute_name: str | None
    metadata: dict[str, Any]


class GraphResponse(BaseModel):
    schema_version: int = 1
    analysis: AnalysisSummary | None = None
    nodes: list[GraphNodeResponse]
    edges: list[GraphEdgeResponse]


class ModuleDependency(BaseModel):
    address: str
    source: str | None
    source_type: str
    version: str | None
    version_type: str
    ref: str | None
    ref_type: str
    immutable: bool
    module_path: list[str]
    expansion: str


class ProviderDependency(BaseModel):
    address: str
    name: str
    source: str | None
    version: str | None
    version_type: str
    module_path: list[str]
    aliases: list[str]


class DependencyResponse(BaseModel):
    modules: list[ModuleDependency]
    providers: list[ProviderDependency]
    analysis: AnalysisSummary | None = None
