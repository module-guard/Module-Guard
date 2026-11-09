from uuid import UUID

from sqlalchemy import select

from app.models import GraphEdge, ParsedEntity
from tests.conftest import publish, sign_in


def test_paths_only_expose_locations_not_values(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client, visibility="PRIVATE")
    sid = UUID(created["scan_id"])
    with sessions() as session:
        e = session.scalar(select(ParsedEntity).where(ParsedEntity.scan_id == sid))
        e.attributes = {"password": "DO_NOT_EXPOSE_TEST_SECRET"}
        edge = session.scalar(select(GraphEdge).where(GraphEdge.scan_id == sid))
        edge.expression = "DO_NOT_EXPOSE_TEST_SECRET"
        session.commit()
    url = f"/api/scans/{sid}/risk-paths"
    response = client.get(url)
    assert response.status_code == 200
    assert "DO_NOT_EXPOSE_TEST_SECRET" not in response.text
    assert response.json()["paths"]
    assert all(p["location"]["file"] for p in response.json()["paths"])
    assert client.get(url, params={"rule": "NOT_PRESENT"}).json()["paths"] == []
    sign_in(client, sessions, "bob", 2)
    assert client.get(url).status_code == 404
