from tests.conftest import publish, sign_in


def test_collect_history_cache_and_visibility(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    publish(client, visibility="PRIVATE")
    url = "/api/repositories/alice/demo/reputation"
    assert client.get(url).json()["entries"] == []
    first = client.post(url + "/collect")
    assert first.status_code == 200, first.text
    assert first.json()["total_score"] is None
    assert first.json() == client.post(url + "/collect").json()
    assert len(client.get(url).json()["entries"]) == 1
    assert client.get(url, params={"limit": 51}).status_code == 422
    sign_in(client, sessions, "bob", 2)
    assert client.get(url).status_code == 404
    assert client.post(url + "/collect").status_code == 404
