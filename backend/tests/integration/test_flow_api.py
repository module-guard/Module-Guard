import os
from unittest.mock import Mock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker
from test_graph_api import service_for

from app.core.database import Base
from app.main import create_app
from app.models import Scan
from app.repositories.graph_repository import GraphRepository
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


def run_fixture(sessions, settings, report, fixture):
    service = service_for(sessions, settings, report, "data_flow/" + fixture)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    return service, accepted.scan_id


def legacy_flows(edges, *, source=None, target=None, edge_type=None, entity_type=None):
    """The previous in-memory /flows filter, kept as the reference for the SQL version."""
    matched = [
        e
        for e in edges
        if (source is None or e.source == source)
        and (target is None or e.target == target)
        and (edge_type is None or e.type == edge_type)
        and (
            entity_type is None
            or any(e.metadata[s]["entity_type"] == entity_type for s in ("source", "target"))
        )
    ]
    summary = {
        "total_flows": len(matched),
        "cross_module_flows": sum(bool(e.metadata.get("cross_module")) for e in matched),
        "unresolved_flows": sum(not e.metadata.get("resolved", False) for e in matched),
        "opaque_external_flows": sum(bool(e.metadata.get("opaque")) for e in matched),
    }
    return matched, summary


def test_trace_path_edges_and_node_fields(sessions, settings, report):
    service, scan_id = run_fixture(sessions, settings, report, "module_input")
    with TestClient(create_app(settings, sessions, service)) as client:
        source, target = "var.bootstrap", "module.compute.aws_instance.web.user_data"
        traced = client.get(
            f"/api/scans/{scan_id}/flow-trace", params={"from": source, "to": target}
        ).json()
        assert traced["found"]
        assert len(traced["path_edges"]) == len(traced["path_edge_ids"])
        for edges, ids, nodes in zip(
            traced["path_edges"], traced["path_edge_ids"], traced["paths"], strict=True
        ):
            assert [e["id"] for e in edges] == ids
            # Each edge links consecutive path nodes.
            assert [e["source"] for e in edges] == [n["address"] for n in nodes[:-1]]
            assert [e["target"] for e in edges] == [n["address"] for n in nodes[1:]]
            assert {"type", "expression", "metadata"} <= set(edges[0])
        first, last = traced["paths"][0][0], traced["paths"][0][-1]
        assert first["source_category"] == "USER_INPUT"
        assert last["sink_category"] == "CODE_EXECUTION"
        assert first["resolved"] and not first["opaque"] and not first["external"]
        assert first["sensitive_input"] is False
        # metadata is still the full endpoint.
        assert first["metadata"]["source_category"] == "USER_INPUT"
        missing = client.get(
            f"/api/scans/{scan_id}/flow-trace", params={"from": source, "to": "nowhere"}
        ).json()
        assert missing["path_edges"] == [] and missing["path_edge_ids"] == []


def test_entity_flow_nodes(sessions, settings, report):
    service, scan_id = run_fixture(sessions, settings, report, "module_input")
    with TestClient(create_app(settings, sessions, service)) as client:
        entity = client.get(f"/api/scans/{scan_id}/flows/local.encoded").json()
        edges = entity["upstream"] + entity["downstream"]
        assert entity["upstream"] and entity["downstream"]
        addresses = [n["address"] for n in entity["nodes"]]
        assert len(addresses) == len(set(addresses))
        assert set(addresses) == {e["source"] for e in edges} | {e["target"] for e in edges}
        nodes = {n["address"]: n for n in entity["nodes"]}
        assert nodes["local.encoded"]["source_category"] == "LOCAL_VALUE"
        assert nodes["var.bootstrap"]["source_category"] == "USER_INPUT"
        empty = client.get(f"/api/scans/{scan_id}/flows/module.compute.aws_instance.web").json()
        assert len(empty["nodes"]) == 2


def test_opaque_and_sensitive_node_fields(sessions, settings, report):
    service, scan_id = run_fixture(sessions, settings, report, "external_module")
    with sessions() as session:
        nodes = FlowService(session).flows(scan_id, limit=100).nodes
        external = [n for n in nodes if n.metadata.get("reason") == "external_module"]
        assert external
        assert all(n.opaque and n.external and not n.resolved for n in external)
    service, scan_id = run_fixture(sessions, settings, report, "sensitive_sinks")
    with sessions() as session:
        flows = FlowService(session).flows(scan_id, source="var.db_password", limit=100)
        nodes = {n.address: n for n in flows.nodes}
        assert nodes["var.db_password"].sensitive_input
        assert nodes["var.db_password"].source_category == "USER_INPUT"
        assert nodes["aws_db_instance.db.password"].sink_category == "CREDENTIAL"
        assert not nodes["aws_db_instance.db.password"].sensitive_input
        assert flows.summary["total_flows"] == 8


def assert_pagination_matches_legacy(sessions, settings, report):
    for fixture in ("sensitive_sinks", "module_output", "external_module", "nested_module"):
        service, scan_id = run_fixture(sessions, settings, report, fixture)
        with sessions() as session:
            flow = FlowService(session)
            # Reference: the previous load path (GraphRepository) + in-memory filtering.
            edges = [
                FlowService.edge(e) for e in GraphRepository(session).edges(scan_id, flow_only=True)
            ]
            assert edges
            filters = [
                {},
                {"source": edges[0].source},
                {"target": edges[-1].target},
                {"edge_type": "SINKS_TO_RESOURCE_ATTRIBUTE"},
                {"edge_type": "PASSES_TO_MODULE_INPUT"},
                {"entity_type": "RESOURCE"},
                {"entity_type": "MODULE"},
                {"entity_type": "VARIABLE", "edge_type": "SINKS_TO_RESOURCE_ATTRIBUTE"},
                {"source": "missing"},
            ]
            for params in filters:
                expected, summary = legacy_flows(edges, **params)
                for limit, offset in ((20, 0), (1, 0), (3, 2), (100, 0), (5, len(expected))):
                    page = flow.flows(scan_id, limit=limit, offset=offset, **params)
                    assert page.summary == summary, (fixture, params)
                    window = expected[offset : offset + limit]
                    assert [e.id for e in page.edges] == [e.id for e in window], (fixture, params)
                    assert {n.address for n in page.nodes} == {
                        a for e in window for a in (e.source, e.target)
                    }


def test_sql_pagination_matches_legacy(sessions, settings, report):
    assert_pagination_matches_legacy(sessions, settings, report)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured"
)
def test_sql_pagination_matches_legacy_postgres(settings, report):
    """Same comparison on PostgreSQL, where metadata is JSONB."""
    base_url = os.environ["TEST_DATABASE_URL"]
    schema = "test_flow_" + uuid4().hex
    admin = create_engine(base_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = (
        make_url(base_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    engine = create_engine(url)
    try:
        Base.metadata.create_all(engine)
        with engine.connect() as connection:
            column_type = connection.execute(
                text(
                    "SELECT data_type FROM information_schema.columns "
                    "WHERE table_schema = :schema AND table_name = 'graph_edges' "
                    "AND column_name = 'metadata'"
                ),
                {"schema": schema},
            ).scalar()
            assert column_type == "jsonb"
        assert_pagination_matches_legacy(
            sessionmaker(engine, expire_on_commit=False), settings, report
        )
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_flows_unknown_scan_is_404(sessions, settings, report):
    service, _ = run_fixture(sessions, settings, report, "basic")
    with TestClient(create_app(settings, sessions, service)) as client:
        assert client.get(f"/api/scans/{uuid4()}/flows?limit=1").status_code == 404
