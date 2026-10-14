from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class EntityType(StrEnum):
    ROOT_MODULE = "ROOT_MODULE"
    MODULE = "MODULE"
    RESOURCE = "RESOURCE"
    DATA = "DATA"
    VARIABLE = "VARIABLE"
    OUTPUT = "OUTPUT"
    LOCAL = "LOCAL"
    PROVIDER = "PROVIDER"
    REQUIRED_PROVIDER = "REQUIRED_PROVIDER"


class TerraformEntity(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    entity_type: EntityType
    address: str
    name: str
    resource_type: str | None = Field(default=None, max_length=255)
    file_path: str
    line_start: int | None = None
    line_end: int | None = None
    # Attribute paths map to exact HCL source expressions, never evaluated values.
    attributes: dict[str, str] = Field(default_factory=dict)
    metadata: dict[str, Any] = Field(default_factory=dict)


class ParseDiagnostic(BaseModel):
    code: str
    file_path: str
    message: str
    module_path: list[str] = Field(default_factory=list)


class TerraformParseResult(BaseModel):
    entities: list[TerraformEntity] = Field(default_factory=list)
    diagnostics: list[ParseDiagnostic] = Field(default_factory=list)
    files_parsed: int = 0
    files_failed: int = 0
    directories_parsed: int = 0


def scope_address(module_path: list[str]) -> str:
    return ".".join(f"module.{name}" for name in module_path)


def qualify(address: str, module_path: list[str]) -> str:
    prefix = scope_address(module_path)
    return f"{prefix}.{address}" if prefix else address
