import pytest
from pydantic import ValidationError

from app.dynamic.adapter import load_fixture
from app.schemas.dynamic import DynamicRun


@pytest.mark.parametrize(
    "status", ["success", "failed", "aborted", "timeout", "not_run", "unobservable"]
)
def test_non_executing_fixtures(status):
    run = load_fixture(status)
    assert not run.execution_occurred
    assert run.mode == "fixture"
    assert run.status == status


@pytest.mark.parametrize("change", ["time", "secret", "runtime", "endpoint", "missing", "success"])
def test_reject_false_observations(change):
    data = load_fixture("success").model_dump(mode="json")
    if change == "time":
        data["stages"][0]["finished_at"] = "2025-01-01T00:00:00Z"
    if change == "secret":
        data["evidence"][0]["raw_log"] = "sensitive"
    if change == "runtime":
        data["execution_occurred"] = True
    if change == "endpoint":
        data["edges"][0]["target"] = "missing"
    if change == "missing":
        data["evidence"] = []
    if change == "success":
        data["stages"][0]["status"] = "failed"
    with pytest.raises(ValidationError):
        DynamicRun.model_validate(data)
