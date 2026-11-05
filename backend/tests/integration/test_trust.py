from uuid import UUID

from app.models import ModuleVersion
from tests.conftest import publish, sign_in


def test_comparison_and_factual_timeline(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    _, scan = publish(client, visibility="PRIVATE")
    detail = client.get("/api/repositories/alice/demo").json()
    version = detail["version"]["version"]
    url = "/api/repositories/alice/demo/trust"
    result = client.get(url + "/compare", params={"before": version, "after": version})
    assert result.status_code == 200, result.text
    body = result.json()
    assert body["manifest_equal"] is True
    assert all(c["change"] == "unchanged" for c in body["changes"])
    assert not body["tool_conditions_equal"]
    assert (
        client.get(
            url + "/compare", params={"before": version, "after": version, "limit": 0}
        ).status_code
        == 422
    )
    timeline = client.get(url + "/timeline", params={"version": version}).json()
    assert timeline["scan_id"] == scan["id"]
    assert timeline["signature_status"] == "unsigned"
    assert not timeline["security_guarantee"]
    assert all(e["at"] for e in timeline["events"])
    assert not any("다운로드" in e["stage"] for e in timeline["events"])
    assert result.headers["cache-control"] == "no-store"
    with sessions() as session:
        v = session.get(ModuleVersion, UUID(timeline["version_id"]))
        v.collected_at = None
        session.commit()
    assert not any(e["stage"] == "수집" for e in client.get(url + "/timeline").json()["events"])
    sign_in(client, sessions, "bob", 2)
    assert client.get(url + "/timeline").status_code == 404
    assert (
        client.get(url + "/compare", params={"before": version, "after": version}).status_code
        == 404
    )
