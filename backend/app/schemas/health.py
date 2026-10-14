from typing import Literal

from pydantic import BaseModel


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    database: Literal["ok", "unavailable"]
    checkov: Literal["available", "unavailable"]
    static_analysis: Literal["available"] = "available"
