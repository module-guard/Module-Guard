from unittest.mock import Mock
from uuid import uuid4

from fastapi.testclient import TestClient
from test_graph_api import service_for

from app.main import create_app
from app.models import Scan
from app.services.analysis_service import StaticAnalysisService
from app.services.flow_service import FlowService


def test_cross_module_trace_and_filters(sessions, settings, report):
    service = service_for(sessions, settings, report, "data_flow/module_input")
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with TestClient(create_app(settings, sessions, service)) as client:
        base = f"/api/scans/{accepted.scan_id}"
        detail = client.get(base).json()
        assert detail["analysis"]["data_flow"]["flows"] == 3
        assert detail["analysis"]["data_flow"]["cross_module_flows"] == 1
        assert detail["summary"]["total"] == 3  # Flow observations are not findings.
        assert detail["analysis"]["stages"]["data_flow"]["status"] == "COMPLETED"
        response = client.get(base + "/flows").json()
        assert response["summary"]["total_flows"] == 3
        nodes = {n["id"] for n in response["nodes"]}
        assert all(e["source"] in nodes and e["target"] in nodes for e in response["edges"])
        source, target = "var.bootstrap", "module.compute.aws_instance.web.user_data"
        traced = client.get(base + "/flow-trace", params={"from": source, "to": target}).json()
        assert traced["found"]
        assert [n["address"] for n in traced["paths"][0]] == [
            source,
            "local.encoded",
            "module.compute.var.user_data",
            target,
        ]
        assert len(traced["path_edge_ids"][0]) == 3
        assert not client.get(
            base + "/flow-trace", params={"from": source, "to": target, "max_depth": 2}
        ).json()["found"]
        assert (
            client.get(
                base + "/flow-trace", params={"from": source, "to": target, "max_depth": 31}
            ).status_code
            == 422
        )
        assert client.get(base + "/flows?limit=101").status_code == 422
        page = client.get(base + "/flows?limit=1&offset=1").json()
        assert len(page["edges"]) == 1 and page["summary"]["total_flows"] == 3
        for filters in (
            {"source": source},
            {"target": target},
            {"edge_type": "PASSES_TO_MODULE_INPUT"},
            {"entity_type": "RESOURCE"},
        ):
            assert len(client.get(base + "/flows", params=filters).json()["edges"]) == 1
        entity = client.get(base + "/flows/module.compute.aws_instance.web").json()
        assert len(entity["upstream"]) == 1 and entity["downstream"] == []
        assert client.get(base + "/flows/missing").status_code == 404
        graph = client.get(base + "/graph").json()
        assert len(graph["edges"]) == detail["analysis"]["dependency_edges"]
        assert not any(e["type"] == "PASSES_TO_MODULE_INPUT" for e in graph["edges"])
        for suffix in ("flows", "flows/var.x", "flow-trace?from=a&to=b"):
            assert client.get(f"/api/scans/{uuid4()}/{suffix}").status_code == 404


def test_nested_outputs_and_opaque(sessions, settings, report):
    for fixture, source, target in [
        ("nested_module", "var.input", "output.result"),
        ("module_output", "module.compute.aws_instance.web.id", "aws_eip.ip.instance"),
        ("external_module", "module.external.subnet_id", "aws_instance.web.subnet_id"),
    ]:
        service = service_for(sessions, settings, report, "data_flow/" + fixture)
        accepted = service.create("https://github.com/org/repo")
        service.run(accepted.scan_id)
        with sessions() as session:
            traced = FlowService(session).trace(accepted.scan_id, source, target)
            assert traced.found
            if fixture == "external_module":
                boundary = FlowService(session).entity(accepted.scan_id, "module.external")
                assert len(boundary.upstream) == len(boundary.downstream) == 1
                assert boundary.upstream[0].metadata["target"]["module_path"] == ["external"]
                assert traced.paths[0][0].metadata["opaque"]
                assert not traced.paths[0][0].metadata["resolved"]


def test_flow_failure_preserves_other_results(sessions, settings, report):
    analysis = StaticAnalysisService()
    analysis.flow_analyzer = Mock()
    analysis.flow_analyzer.analyze.side_effect = RuntimeError("sensitive value")
    service = service_for(sessions, settings, report, analysis=analysis)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        assert scan.status == "COMPLETED" and scan.partial_result
        assert scan.total_findings == 3
        assert scan.analysis["stages"]["data_flow"]["status"] == "FAILED"
        assert "sensitive value" not in str(scan.analysis)
        assert FlowService(session).flows(scan.id).edges == []


def test_legacy_empty_flows(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    with sessions() as session:
        flow = FlowService(session)
        assert flow.flows(accepted.scan_id).summary["total_flows"] == 0
        assert not flow.trace(accepted.scan_id, "a", "b").found
