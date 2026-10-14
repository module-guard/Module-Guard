from typing import Any

from pydantic import BaseModel, ConfigDict, Field


class UsageRequest(BaseModel):
    """Form values for one module version. Values are data; see app.usage.hcl."""

    model_config = ConfigDict(extra="forbid")
    version: str | None = Field(default=None, max_length=100)
    values: dict[str, Any] = Field(default_factory=dict, max_length=2000)
    # Optional sensitive variables to pass through as var.<name>; required ones always are.
    pass_sensitive: list[str] = Field(default_factory=list, max_length=2000)


class UsagePreview(BaseModel):
    version: str
    filename: str
    main_tf: str
    variables_tf: str | None
    missing_required: list[str]
    sensitive: list[str]
    git_source: str | None
