from typing import Any

from pydantic import BaseModel

from app.schemas.finding import FindingResponse


class CorrelationDetail(BaseModel):
    finding: FindingResponse
    evidence: dict[str, Any]
    attack_path: list[dict[str, Any]]
