from datetime import datetime
from typing import Any, Literal
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field

from app.models.enums import Severity


class NormalizedFinding(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    rule_id: str = Field(min_length=1, max_length=255)
    scanner: str = "checkov"
    severity: Severity
    title: str
    resource: str | None = None
    resource_type: str | None = Field(default=None, max_length=255)
    file_path: str | None = None
    line_start: int | None = Field(default=None, ge=1)
    line_end: int | None = Field(default=None, ge=1)
    description: str | None = None
    guideline: str | None = None
    check_result: str = "FAILED"
    raw_data: dict[str, Any] = Field(default_factory=dict)
    # MODULE: the module body. EXAMPLE: found in, or instantiated from, examples/tests.
    scope: Literal["MODULE", "EXAMPLE"] = "MODULE"


class FindingResponse(NormalizedFinding):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    scan_id: UUID
    created_at: datetime
