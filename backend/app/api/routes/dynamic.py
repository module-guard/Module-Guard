from fastapi import APIRouter, Response

from app.dynamic.adapter import Scenario, load_fixture
from app.schemas.dynamic import DynamicRun

router = APIRouter(prefix="/dynamic-demo", tags=["dynamic-demo"])


@router.get("/{scenario}", response_model=DynamicRun)
def demo(scenario: Scenario, response: Response):
    response.headers["Cache-Control"] = "no-store"
    return load_fixture(scenario)
