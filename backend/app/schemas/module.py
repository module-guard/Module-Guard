from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, computed_field

from app.models.enums import SourceType


class ModuleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)
    id: UUID
    name: str
    source_type: SourceType
    source_url: str
    repository_owner: str | None
    repository_name: str | None
    default_branch: str | None
    provider: str | None
    latest_version: str | None
    created_at: datetime
    updated_at: datetime

    @computed_field
    @property
    def is_demo(self) -> bool:
        return self.source_url.startswith("fixture://")
