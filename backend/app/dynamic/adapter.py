from pathlib import Path
from typing import Literal

from app.schemas.dynamic import DynamicRun

Scenario = Literal["success", "failed", "aborted", "timeout", "not_run", "unobservable"]


def load_fixture(scenario: Scenario) -> DynamicRun:
    if scenario not in ("success", "failed", "aborted", "timeout", "not_run", "unobservable"):
        raise ValueError("Unknown scenario")
    run = DynamicRun.model_validate_json(
        (Path(__file__).parent / "fixtures" / f"{scenario}.json").read_bytes()
    )
    if run.mode != "fixture" or run.execution_occurred:
        raise ValueError("Only non-executing fixtures are served")
    return run
