import secrets
from datetime import timedelta
from unittest.mock import Mock

from conftest import MODULE, prepare, publish, register, sign_in, wait_terminal
from sqlalchemy import select

from app.models import Scan
from app.models.common import utcnow


def home(client):
    response = client.get("/api/me/home")
    assert response.status_code == 200, response.text
    return response.json()


def test_home_needs_login(stack):
    assert stack().get("/api/me/home").status_code == 401


def test_home_shows_only_my_repositories_and_what_needs_a_look(stack, sessions, report):
    parked = stack(dispatcher=Mock())
    sign_in(parked, sessions, "alice", 1)
    register(parked, prepare(parked)["upload_id"], "running")
    client = stack()
    sign_in(client, sessions, "bob", 2)
    publish(client, "bobs")
    sign_in(client, sessions, "alice", 1)
    _, scanned = publish(client, "vpc")
    publish(client, "secret", visibility="PRIVATE")
    publish(client, "old")
    # The snapshot cannot be stored, so the attempt fails and is never published.
    small = stack(max_snapshot_size_mb=1)
    sign_in(small, sessions, "alice", 1)
    noise = "# " + secrets.token_hex(700_000) + "\n"
    created = register(small, prepare(small, {**MODULE, "big.tf": noise})["upload_id"], "broken")
    assert wait_terminal(small, created.json()["scan_id"])["status"] == "FAILED"
    assert client.post("/api/repositories/bob/bobs/fork", json={}).status_code == 201
    # Make one scan look three months old.
    with sessions() as session:
        old = session.scalars(select(Scan).order_by(Scan.created_at)).all()
        target = next(
            s
            for s in old
            if s.module_version
            and s.module_version.repository
            and s.module_version.repository.name == "old"
        )
        target.finished_at = utcnow() - timedelta(days=91)
        session.commit()

    page = home(client)
    names = {card["name"]: card for card in page["repositories"]}
    assert set(names) == {"running", "vpc", "secret", "old", "broken", "bobs"}
    assert names["secret"]["visibility"] == "PRIVATE" and names["running"]["state"] == "PENDING"
    assert names["broken"]["state"] == "FAILED" and names["bobs"]["forked_from"]["owner"] == "bob"
    assert {(a["name"], a["reason"]) for a in page["attention"]} == {
        ("broken", "failed"),
        ("old", "stale"),
    }
    assert page["stale_after_days"] == 90
    kinds = [(a["name"], a["kind"]) for a in page["activity"]]
    assert len(kinds) == 5 and kinds[0] == ("bobs", "forked")
    assert ("broken", "failed") in kinds and ("secret", "published") in kinds
    # Severity adds up the newest stored version of each of my repositories (body only).
    published = [c for c in names.values() if c["badge"]]
    assert page["severity"]["total"] == sum(c["badge"]["total"] for c in published)
    assert page["severity"]["total"] == scanned["summary"]["total"] * 4  # vpc, secret, old, bobs

    sign_in(client, sessions, "bob", 2)
    assert [c["name"] for c in home(client)["repositories"]] == ["bobs"]
    assert home(client)["attention"] == []
