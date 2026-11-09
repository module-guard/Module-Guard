from fastapi.testclient import TestClient

from app.main import create_app
from app.schemas.dynamic import DynamicRun


def test_demo_api_contract(settings, sessions):
    client = TestClient(create_app(settings, sessions))
    for scenario in ("success", "failed", "aborted", "timeout", "not_run", "unobservable"):
        response = client.get("/api/dynamic-demo/" + scenario)
        assert response.status_code == 200
        run = DynamicRun.model_validate(response.json())
        assert run.mode == "fixture"
        assert run.execution_occurred is False
        assert response.headers["cache-control"] == "no-store"
    assert client.get("/api/dynamic-demo/other").status_code == 422
