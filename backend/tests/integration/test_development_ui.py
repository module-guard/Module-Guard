import time

from fastapi.testclient import TestClient

from app.main import create_app


def test_production_has_no_demo_route_or_direct_scan(settings, sessions):
    settings.app_environment = "production"
    with TestClient(create_app(settings, sessions)) as client:
        assert client.post("/api/dev/demo/correlation").status_code == 404
        # Anonymous direct scans would bypass the publish checks.
        direct = client.post("/api/scans", json={"source": "https://github.com/org/repo"})
        assert direct.status_code == 404
        # The old list endpoints are gone in every environment.
        for path in ("/api/scans", "/api/modules", "/api/dashboard"):
            assert client.get(path).status_code in (404, 405)
        assert "/api/dev/demo/correlation" not in client.get("/openapi.json").json()["paths"]
        health = client.get("/api/health").json()
        assert health["database"] == "ok"
        assert health["static_analysis"] == "available"


def test_development_demo_runs_real_pipeline(settings, sessions):
    settings.app_environment = "development"
    with TestClient(create_app(settings, sessions)) as client:
        response = client.post("/api/dev/demo/correlation")
        assert response.status_code == 202
        scan_id = response.json()["scan_id"]
        base = f"/api/scans/{scan_id}"
        for _ in range(100):
            scan = client.get(base).json()
            if scan["status"] in {"COMPLETED", "FAILED"}:
                break
            time.sleep(0.05)
        assert scan["status"] == "COMPLETED", scan
        assert scan["module"]["is_demo"] is True
        assert scan["module"]["source_url"].startswith("fixture://")
        assert scan["analysis"]["scanner_breakdown"] == {
            "checkov": 1,
            "source_trust": 2,
            "correlation": 1,
        }
        risks = client.get(base + "/correlations").json()
        assert len(risks) == 1
        assert risks[0]["raw_data"]["development_fixture"] is True
        assert risks[0]["rule_id"] == "TFCR001"
        assert risks[0]["severity"] == "CRITICAL"
        assert risks[0]["raw_data"]["confidence"] == "MEDIUM"
        detail = client.get(base + "/correlations/" + risks[0]["id"]).json()
        assert len(detail["evidence"]["edges"]) == 2
        assert client.get(
            base + "/flow-trace",
            params={
                "from": "module.policy_source.policy",
                "to": "aws_iam_role_policy.admin.policy",
            },
        ).json()["found"]
        assert (
            client.post("/api/scans", json={"source": "fixture://correlation/iam"}).status_code
            >= 400
        )
