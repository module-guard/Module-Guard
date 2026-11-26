from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from conftest import ORIGIN, publish, sign_in

from app.auth.dependencies import require_admin
from app.core.exceptions import AdminRequiredError

BASE = "/api/admin/allowblock/entries"
OWNER_BLOCK = {
    "target_kind": "github_owner",
    "target": "Evil-Org",
    "effect": "BLOCK",
    "reason": "internal triage notes",
    "evidence_urls": ["https://example.com/evidence"],
}


@pytest.fixture
def client(stack, sessions):
    client = stack(admin_logins=["Root"])
    client.sessions = sessions
    return client


def as_admin(client):
    return sign_in(client, client.sessions, "root", 100)


def as_user(client, login="alice", github_id=1):
    return sign_in(client, client.sessions, login, github_id)


def create(client, **over):
    return client.post(BASE, json={**OWNER_BLOCK, **over})


def test_anonymous_and_non_admins_cannot_write_or_read(client):
    client.headers["Origin"] = ORIGIN
    assert create(client).status_code == 401
    assert client.get(BASE).status_code == 401
    as_user(client)
    assert create(client).status_code == 403
    assert create(client).json()["error"]["code"] == "AdminRequiredError"
    assert client.get(BASE).status_code == 403
    assert (
        client.post(f"{BASE}/{'0' * 8}-0000-0000-0000-{'0' * 12}/revoke", json={}).status_code
        == 403
    )


def test_admin_login_is_compared_without_case(client):
    sign_in(client, client.sessions, "ROOT", 100)
    assert create(client).status_code == 201


def test_system_accounts_are_never_admins():
    system = SimpleNamespace(login="root", is_system=True)
    request = SimpleNamespace(
        app=SimpleNamespace(state=SimpleNamespace(settings=SimpleNamespace(admin_logins=["root"])))
    )
    with pytest.raises(AdminRequiredError):
        require_admin(request, system)


def test_writes_need_an_allowed_origin(client):
    as_admin(client)
    del client.headers["Origin"]
    assert create(client).status_code == 403
    client.headers["Origin"] = "https://evil.example"
    assert create(client).status_code == 403
    client.headers["Origin"] = ORIGIN
    assert create(client).status_code == 201


def test_create_returns_the_admin_view(client):
    as_admin(client)
    response = create(
        client,
        public_message="Blocked",
        expires_at=(datetime.now(UTC) + timedelta(days=1)).isoformat(),
    )
    assert response.status_code == 201, response.text
    body = response.json()
    assert body["target_key"] == "evil-org" and body["status"] == "ACTIVE"
    assert body["reason"] == "internal triage notes"
    assert body["created_by_login"] == "root"
    assert body["public_message"] == "Blocked" and body["expires_at"]
    assert client.get(f"{BASE}/{body['id']}").json()["id"] == body["id"]


def test_invalid_input_is_rejected(client):
    as_admin(client)
    assert create(client, effect="DENY").status_code == 422
    assert create(client, reason="").status_code == 422
    assert create(client, target_kind="glob", target="*").status_code == 422
    assert create(client, target="a/b").status_code == 422
    assert create(client, evidence_urls=["javascript:alert(1)"]).status_code == 422
    assert create(client, evidence_urls=["https://e.test"] * 11).status_code == 422
    assert (
        create(client, expires_at=(datetime.now(UTC) - timedelta(days=1)).isoformat()).status_code
        == 422
    )
    assert create(client, expires_at="2099-01-01T00:00:00").status_code == 422
    assert create(client, surprise=True).status_code == 422
    assert create(client, target_kind="repository", target="not-a-uuid").status_code == 422
    assert (
        create(
            client, target_kind="repository", target="1" * 8 + "-1111-1111-1111-" + "1" * 12
        ).status_code
        == 404
    )
    assert client.get(BASE).json()["entries"] == []


def test_duplicate_active_entry_conflicts(client):
    as_admin(client)
    assert create(client).status_code == 201
    assert create(client, target="evil-org").status_code == 409
    assert create(client, effect="ALLOW").status_code == 201


def test_list_filters_and_pages(client):
    as_admin(client)
    for index in range(3):
        assert create(client, target=f"org-{index}").status_code == 201
    assert create(client, target="org-0", effect="ALLOW").status_code == 201
    page = client.get(BASE, params={"limit": 2}).json()
    assert len(page["entries"]) == 2 and page["next_offset"] == 2
    rest = client.get(BASE, params={"limit": 2, "offset": 2}).json()
    assert len(rest["entries"]) == 2 and rest["next_offset"] is None
    allow = client.get(BASE, params={"effect": "ALLOW"}).json()["entries"]
    assert [e["target_key"] for e in allow] == ["org-0"]
    assert client.get(BASE, params={"target_kind": "repository"}).json()["entries"] == []
    assert client.get(BASE, params={"limit": 51}).status_code == 422


def test_revoke_and_history(client):
    as_admin(client)
    entry = create(client).json()
    revoked = client.post(
        f"{BASE}/{entry['id']}/revoke",
        json={"reason": "reviewed again", "evidence_urls": ["https://example.com/2"]},
    )
    assert revoked.status_code == 200, revoked.text
    assert revoked.json()["status"] == "REVOKED"
    assert revoked.json()["revoke_reason"] == "reviewed again"
    assert client.post(f"{BASE}/{entry['id']}/revoke", json={"reason": "again"}).status_code == 409
    assert (
        client.post(
            f"{BASE}/{'1' * 8}-1111-1111-1111-{'1' * 12}/revoke", json={"reason": "x"}
        ).status_code
        == 404
    )
    history = client.get(f"{BASE}/{entry['id']}/events").json()
    assert [e["action"] for e in history["events"]] == ["CREATE", "REVOKE"]
    assert history["events"][0]["actor_login"] == "root"
    assert history["events"][1]["snapshot"]["revoked_at"]
    assert client.get(BASE).json()["entries"] == []
    assert len(client.get(BASE, params={"status": "all"}).json()["entries"]) == 1
    assert client.get(f"{BASE}/{'1' * 8}-1111-1111-1111-{'1' * 12}/events").status_code == 404


def test_there_is_no_way_to_edit_or_delete_an_entry_or_event(client):
    as_admin(client)
    entry = create(client).json()
    for method in ("put", "patch", "delete"):
        assert getattr(client, method)(f"{BASE}/{entry['id']}").status_code == 405
        assert getattr(client, method)(f"{BASE}/{entry['id']}/events").status_code == 405


def test_admin_writes_are_rate_limited_per_admin(stack, sessions):
    client = stack(admin_logins=["root"], allowblock_writes_per_hour=2)
    sign_in(client, sessions, "root", 100)
    assert create(client, target="one").status_code == 201
    assert create(client, target="two").status_code == 201
    limited = create(client, target="three")
    assert limited.status_code == 429
    assert client.get(BASE).status_code == 200  # reads are not counted


def test_public_views_show_only_visible_repositories_and_no_internals(client, sessions):
    as_user(client, "alice", 1)
    public, _ = publish(client, "demo")
    private, _ = publish(client, "secret", visibility="PRIVATE")
    admin_id = as_admin(client)
    from sqlalchemy import select

    from app.models import Repository

    with sessions() as session:
        ids = {r.name: str(r.id) for r in session.scalars(select(Repository))}
    for name in ("demo", "secret"):
        assert (
            create(
                client,
                target_kind="repository",
                target=ids[name],
                reason="private reason",
                public_message="Blocked: abuse" if name == "demo" else None,
            ).status_code
            == 201
        )
    client.cookies.clear()
    client.headers.pop("Origin", None)
    listing = client.get("/api/allowblock/entries")
    assert listing.status_code == 200
    assert listing.headers["cache-control"] == "private, no-store"
    page = listing.json()
    assert [(e["repository"]["owner"], e["repository"]["name"]) for e in page["entries"]] == [
        ("alice", "demo")
    ]
    entry = page["entries"][0]
    assert (
        entry["effect"] == "BLOCK"
        and entry["status"] == "ACTIVE"
        and entry["message"] == "Blocked: abuse"
    )
    text = listing.text
    for secret in ("private reason", "evidence", "root", "created_by", 'reason"'):
        assert secret not in text
    policy = client.get("/api/repositories/alice/demo/policy")
    assert policy.status_code == 200 and policy.headers["cache-control"] == "private, no-store"
    assert policy.json()["effect"] == "BLOCK"
    assert "reason" not in policy.text and "evidence" not in policy.text
    hidden = client.get("/api/repositories/alice/secret/policy")
    missing = client.get("/api/repositories/alice/nothing/policy")
    assert hidden.status_code == missing.status_code == 404
    assert hidden.json() == missing.json()
    # The owner can see the policy of the private repository.
    as_user(client, "alice", 1)
    assert client.get("/api/repositories/alice/secret/policy").json()["entries"][0]["message"]
    assert admin_id and private and public


def test_repository_policy_reports_none_allow_and_block_precedence(client, sessions):
    as_user(client, "alice", 1)
    publish(client, "demo")
    client.cookies.clear()
    assert client.get("/api/repositories/alice/demo/policy").json() == {
        "repository": {"owner": "alice", "name": "demo"},
        "effect": "NONE",
        "entries": [],
    }
    from sqlalchemy import select

    from app.models import Repository

    with sessions() as session:
        repository_id = str(session.scalar(select(Repository.id)))
    as_admin(client)
    create(client, target_kind="repository", target=repository_id, effect="ALLOW")
    assert client.get("/api/repositories/alice/demo/policy").json()["effect"] == "ALLOW"
    create(client, target_kind="repository", target=repository_id, effect="BLOCK")
    body = client.get("/api/repositories/alice/demo/policy").json()
    assert body["effect"] == "BLOCK" and len(body["entries"]) == 2
    messages = {e["message"] for e in body["entries"]}
    assert all(messages)  # a fixed generic text stands in when public_message is unset


def test_public_listing_is_paginated_after_visibility(client, sessions):
    as_user(client, "alice", 1)
    for index in range(3):
        publish(client, f"mod{index}")
    publish(client, "hidden", visibility="PRIVATE")
    from sqlalchemy import select

    from app.models import Repository

    with sessions() as session:
        ids = [str(r.id) for r in session.scalars(select(Repository).order_by(Repository.name))]
    as_admin(client)
    for repository_id in ids:
        assert create(client, target_kind="repository", target=repository_id).status_code == 201
    client.cookies.clear()
    first = client.get("/api/allowblock/entries", params={"limit": 2}).json()
    assert len(first["entries"]) == 2 and first["next_offset"] == 2
    second = client.get("/api/allowblock/entries", params={"limit": 2, "offset": 2}).json()
    assert len(second["entries"]) == 1 and second["next_offset"] is None
