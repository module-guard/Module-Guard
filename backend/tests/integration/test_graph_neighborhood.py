from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import event
from test_graph_api import service_for

from app.graphs.domain import DependencyEdge, DependencyGraph
from app.main import create_app
from app.parsers.domain import EntityType, TerraformEntity
from app.repositories.graph_repository import GraphRepository


@pytest.fixture
def neighborhood(sessions, settings, report):
    service = service_for(sessions, settings, report)
    scan_id = service.create("https://github.com/org/neighborhood").scan_id
    nodes = {
        name: TerraformEntity(
            entity_type=EntityType.LOCAL,
            address=f"local.{name}",
            name=name,
            file_path="main.tf",
        )
        for name in ("a", "b", "c", "d", "parent", "flow_only")
    }
    edges = [
        DependencyEdge(
            source_entity_id=nodes[source].id,
            target_entity_id=nodes[target].id if target in nodes else None,
            target_address=nodes[target].address if target in nodes else target,
            edge_type=kind,
        )
        for source, target, kind in (
            ("a", "b", "READS_LOCAL"),
            ("a", "b", "REFERENCES"),
            ("b", "c", "READS_LOCAL"),
            ("c", "d", "READS_LOCAL"),
            ("parent", "a", "READS_LOCAL"),
            ("a", "a", "READS_LOCAL"),
            ("a", "var.missing", "READS_VARIABLE"),
            ("a", "flow_only", "FLOWS_TO"),
        )
    ]
    with sessions() as session:
        GraphRepository(session).save(
            scan_id, DependencyGraph(nodes=list(nodes.values()), edges=edges)
        )
        session.commit()
    with TestClient(create_app(settings, sessions, service)) as client:
        yield client, f"/api/scans/{scan_id}/graph", scan_id, nodes


def fetch(neighborhood, **params):
    client, base, _, _ = neighborhood
    response = client.get(base + "/neighborhood", params={"address": "local.a", **params})
    assert response.status_code == 200, response.text
    data = response.json()
    ids = {node["id"] for node in data["nodes"]}
    assert all(edge["source"] in ids and edge["target"] in ids for edge in data["edges"])
    assert all(edge["type"] != "FLOWS_TO" for edge in data["edges"])
    return data


@pytest.mark.parametrize(
    "direction,depth,expected",
    [
        ("out", 1, ["local.a", "local.b", "var.missing"]),
        ("out", 2, ["local.a", "local.b", "var.missing", "local.c"]),
        ("out", 3, ["local.a", "local.b", "var.missing", "local.c", "local.d"]),
        ("in", 3, ["local.a", "local.parent"]),
        ("both", 1, ["local.a", "local.b", "local.parent", "var.missing"]),
    ],
)
def test_depth_direction_and_bfs_order(neighborhood, direction, depth, expected):
    data = fetch(neighborhood, direction=direction, depth=depth)
    assert [node["address"] for node in data["nodes"]] == expected
    assert data["center"] == str(neighborhood[3]["a"].id)
    assert data["schema_version"] == 1 and data["direction"] == direction
    assert data["depth"] == depth and not data["truncated"]


def test_defaults_full_graph_route_and_unresolved_center(neighborhood):
    client, base, _, _ = neighborhood
    data = fetch(neighborhood)
    assert data["depth"] == 1 and data["direction"] == "both"
    graph = client.get(base)
    assert graph.status_code == 200
    graph = graph.json()
    assert "center" not in graph and "analysis" in graph
    full_edges = {edge["id"]: edge for edge in graph["edges"]}
    assert all(edge == full_edges[edge["id"]] for edge in data["edges"])
    placeholder = next(node for node in data["nodes"] if node["type"] == "UNRESOLVED")
    assert placeholder["id"] == "unresolved:var.missing"
    assert placeholder["module_path"] == []
    assert placeholder["name"] is None and placeholder["resource_type"] is None
    reverse = fetch(neighborhood, address="var.missing", direction="in")
    assert reverse["center"] == placeholder["id"]
    assert [node["address"] for node in reverse["nodes"]] == ["var.missing", "local.a"]


def test_repeated_edge_types_filter_traversal_and_returned_edges(neighborhood):
    data = fetch(neighborhood, edge_type=["READS_LOCAL", "READS_VARIABLE"], direction="out")
    assert {node["address"] for node in data["nodes"]} == {"local.a", "local.b", "var.missing"}
    assert {edge["type"] for edge in data["edges"]} == {"READS_LOCAL", "READS_VARIABLE"}
    for kind in ("FLOWS_TO", "NO_SUCH_TYPE"):
        isolated = fetch(neighborhood, edge_type=kind)
        assert len(isolated["nodes"]) == 1 and isolated["edges"] == []
        assert not isolated["truncated"]


@pytest.mark.parametrize(
    "limit,truncated", [(1, True), (2, True), (3, True), (4, False), (5, False)]
)
def test_limit_and_exact_boundary(neighborhood, limit, truncated):
    data = fetch(neighborhood, direction="out", depth=3, edge_type="READS_LOCAL", limit=limit)
    assert [node["address"] for node in data["nodes"]] == [
        f"local.{name}" for name in "abcd"[:limit]
    ]
    assert data["truncated"] == truncated
    # A depth boundary by itself is not truncation.
    assert not fetch(neighborhood, direction="out", depth=1, limit=3)["truncated"]


def test_missing_scan_center_and_legacy_graph(neighborhood, sessions):
    client, base, scan_id, _ = neighborhood
    for address in ("missing", ""):
        assert client.get(base + "/neighborhood", params={"address": address}).status_code == 404
    assert (
        client.get(
            f"/api/scans/{uuid4()}/graph/neighborhood", params={"address": "local.a"}
        ).status_code
        == 404
    )
    with sessions() as session:
        from app.models import Scan

        scan = session.get(Scan, scan_id)
        other = Scan(module_id=scan.module_id)
        session.add(other)
        session.commit()
        legacy_id = other.id
    assert client.get(f"/api/scans/{legacy_id}/graph").json()["nodes"] == []
    assert (
        client.get(
            f"/api/scans/{legacy_id}/graph/neighborhood", params={"address": "local.a"}
        ).status_code
        == 404
    )


@pytest.mark.parametrize(
    "params",
    [
        {},
        {"address": "local.a", "depth": 0},
        {"address": "local.a", "depth": 4},
        {"address": "local.a", "depth": "bad"},
        {"address": "local.a", "direction": "up"},
        {"address": "local.a", "limit": 0},
        {"address": "local.a", "limit": 501},
    ],
)
def test_query_validation(neighborhood, params):
    client, base, _, _ = neighborhood
    assert client.get(base + "/neighborhood", params=params).status_code == 422


def test_default_and_max_limit_load_edges_once(neighborhood, sessions):
    _, _, scan_id, nodes = neighborhood
    more = [
        TerraformEntity(
            entity_type=EntityType.LOCAL,
            address=f"local.x{i:03}",
            name=f"x{i:03}",
            file_path="main.tf",
        )
        for i in range(510)
    ]
    with sessions() as session:
        GraphRepository(session).save(
            scan_id,
            DependencyGraph(
                nodes=more,
                edges=[
                    DependencyEdge(
                        source_entity_id=nodes["a"].id,
                        target_entity_id=node.id,
                        target_address=node.address,
                        edge_type="READS_LOCAL",
                    )
                    for node in more
                ],
            ),
        )
        session.commit()
    queries = []

    def capture(connection, cursor, statement, parameters, context, many):
        if "from graph_edges" in statement.lower():
            queries.append(statement)

    engine = sessions.kw["bind"]
    event.listen(engine, "before_cursor_execute", capture)
    try:
        data = fetch(neighborhood)
        assert len(data["nodes"]) == 150 and data["truncated"]
        assert len(queries) == 1
        queries.clear()
        data = fetch(neighborhood, limit=500)
        assert len(data["nodes"]) == 500 and data["truncated"]
        assert len(queries) == 1
    finally:
        event.remove(engine, "before_cursor_execute", capture)
