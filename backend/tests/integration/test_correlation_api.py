from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import select
from test_graph_api import service_for

from app.main import create_app
from app.models import Finding, GraphEdge, Scan


def iam_report():
    return {
        "check_type": "terraform",
        "results": {
            "failed_checks": [
                {
                    "check_id": "CKV_AWS_62",
                    "check_name": "IAM administrative privileges",
                    "resource": "aws_iam_role_policy.admin",
                    "severity": "HIGH",
                    "file_path": "/main.tf",
                    "check_result": {"result": "FAILED", "evaluated_keys": ["policy"]},
                }
            ]
        },
        "summary": {"failed": 1},
    }


def test_correlation_pipeline_api_evidence_dashboard(sessions, settings):
    service = service_for(sessions, settings, iam_report(), "correlation/mutable_git_to_iam")
    scan = service.create("https://github.com/org/repo")
    service.run(scan.scan_id)
    service.run(scan.scan_id)
    with TestClient(create_app(settings, sessions, service)) as client:
        base = f"/api/scans/{scan.scan_id}"
        detail = client.get(base).json()
        assert detail["status"] == "COMPLETED"
        assert detail["analysis"]["stages"]["correlation"]["status"] == "COMPLETED"
        assert detail["analysis"]["correlation"]["rules_evaluated"] == 8
        assert detail["analysis"]["correlation"]["findings"] == 1
        assert detail["summary"]["total"] == 4
        results = client.get(base + "/correlations").json()
        assert len(results) == 1, results
        finding = results[0]
        assert finding["rule_id"] == "TFCR001" and finding["severity"] == "CRITICAL"
        assert client.get(base + "/findings?scanner=correlation").json() == results
        for params in (
            {"rule_id": "TFCR001"},
            {"severity": "CRITICAL"},
            {"confidence": "MEDIUM"},
            {"source": "module.policy_source.policy"},
            {"sink": "aws_iam_role_policy.admin.policy"},
        ):
            assert len(client.get(base + "/correlations", params=params).json()) == 1
        assert client.get(base + "/correlations?confidence=LOW").json() == []
        assert client.get(base + "/correlations?confidence=INVALID").status_code == 422
        assert client.get(base + "/correlations?limit=101").status_code == 422
        assert client.get(base + "/correlations?offset=1").json() == []
        evidence = client.get(base + "/correlations/" + finding["id"]).json()
        assert {f["rule_id"] for f in evidence["evidence"]["source_trust"]} == {
            "TFSC004",
            "TFSC006",
        }
        assert evidence["evidence"]["checkov"][0]["rule_id"] == "CKV_AWS_62"
        assert len(evidence["evidence"]["edges"]) == 2
        assert (
            evidence["evidence"]["missing_finding_ids"]
            == evidence["evidence"]["missing_edge_ids"]
            == []
        )
        assert evidence["attack_path"][-1]["type"] == "CHECKOV"
        other = service.create("https://github.com/org/other")
        assert (
            client.get(f"/api/scans/{other.scan_id}/correlations/{finding['id']}").status_code
            == 404
        )
        assert client.get(f"/api/scans/{uuid4()}/correlations").status_code == 404
        assert client.get(base + f"/correlations/{uuid4()}").status_code == 404
        stored = client.get(base).json()
        assert stored["analysis"]["scanner_breakdown"] == {
            "checkov": 1,
            "source_trust": 2,
            "correlation": 1,
        }
        assert stored["summary"]["total"] == 4
        assert [r["severity"] for r in client.get(base + "/correlations").json()] == ["CRITICAL"]
        trace = client.get(
            base + "/flow-trace",
            params={
                "from": "module.policy_source.policy",
                "to": "aws_iam_role_policy.admin.policy",
            },
        ).json()
        assert evidence["evidence"]["flow_edge_ids"] in trace["path_edge_ids"]
    with sessions() as session:
        ids = {
            str(f.id)
            for f in session.scalars(select(Finding).where(Finding.scan_id == scan.scan_id))
        }
        edges = {
            str(e.id)
            for e in session.scalars(select(GraphEdge).where(GraphEdge.scan_id == scan.scan_id))
        }
        assert (
            set(
                finding["raw_data"]["related_finding_ids"]
                + finding["raw_data"]["source_finding_ids"]
            )
            <= ids
        )
        assert set(finding["raw_data"]["flow_edge_ids"]) <= edges


def test_correlation_failure_preserves_all_prior_findings(sessions, settings):
    service = service_for(sessions, settings, iam_report(), "correlation/mutable_git_to_iam")
    service.correlation_engine = Mock()
    service.correlation_engine.evaluate.side_effect = RuntimeError("sensitive literal")
    scan = service.create("https://github.com/org/repo")
    service.run(scan.scan_id)
    with sessions() as session:
        row = session.get(Scan, scan.scan_id)
        assert row.status == "COMPLETED" and row.partial_result
        assert row.total_findings == 3
        assert row.analysis["stages"]["correlation"]["status"] == "FAILED"
        assert "sensitive literal" not in str(row.analysis)
        assert row.analysis["data_flow"]["flows"] == 2


def test_negative_and_legacy_api(sessions, settings):
    service = service_for(sessions, settings, iam_report(), "correlation/unrelated_findings")
    scanned = service.create("https://github.com/org/repo")
    service.run(scanned.scan_id)
    legacy = service.create("https://github.com/org/legacy")
    with TestClient(create_app(settings, sessions, service)) as client:
        assert client.get(f"/api/scans/{scanned.scan_id}/correlations").json() == []
        assert client.get(f"/api/scans/{legacy.scan_id}/correlations").json() == []
        assert client.get(f"/api/scans/{legacy.scan_id}").json()["analysis"] is None
