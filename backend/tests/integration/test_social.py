import time
from unittest.mock import Mock

import pytest
from conftest import MODULE, ORIGIN, prepare, publish, register, sign_in
from sqlalchemy import func, select

from app.models import Module, ModuleVersion, Repository, Scan
from app.storage.retention import sweep_unreferenced


def detail(client, owner, name, **params):
    return client.get(f"/api/repositories/{owner}/{name}", params=params)


def snapshots(settings):
    root = settings.snapshot_root
    return sorted(p.name for p in root.glob("*.tar.gz")) if root.exists() else []


def sweep(client, sessions, grace=0):
    with sessions() as session:
        return sweep_unreferenced(session, client.scans.snapshot_store, grace)


def test_list_endpoints_cap_the_page_size_at_fifty(stack, sessions):
    client = stack()
    sign_in(client, sessions, "alice", 1)
    for path in ("/api/repositories", "/api/users/alice/repositories"):
        assert client.get(path, params={"limit": 50}).status_code == 200
        assert client.get(path, params={"limit": 51}).status_code == 422


def test_owner_can_edit_and_delete_a_published_repository(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    created, _ = publish(client, "demo")
    assert (
        client.patch(
            "/api/repositories/demo", json={"visibility": "PRIVATE", "description": "Now hidden"}
        ).status_code
        == 204
    )
    page = detail(client, "alice", "demo").json()
    assert page["visibility"] == "PRIVATE" and page["description"] == "Now hidden"
    # Only the fields that were sent change.
    assert client.patch("/api/repositories/demo", json={"visibility": "PUBLIC"}).status_code == 204
    assert detail(client, "alice", "demo").json()["description"] == "Now hidden"
    assert client.patch("/api/repositories/demo", json={"description": ""}).status_code == 204
    assert detail(client, "alice", "demo").json()["description"] is None
    assert client.patch("/api/repositories/demo", json={"owner_id": "x"}).status_code == 422

    sign_in(client, sessions, "mallory", 2)
    assert client.patch("/api/repositories/demo", json={"visibility": "PRIVATE"}).status_code == 404
    assert client.delete("/api/repositories/demo").status_code == 404
    client.cookies.clear()
    assert client.patch("/api/repositories/demo", json={}).status_code == 401
    sign_in(client, sessions)
    refused = client.patch(
        "/api/repositories/demo", json={}, headers={"Origin": "https://evil.com"}
    )
    assert refused.status_code == 403

    assert len(snapshots(settings)) == 1
    assert client.delete("/api/repositories/demo?confirm=demo").status_code == 204
    assert detail(client, "alice", "demo").status_code == 404
    assert client.get(f"/api/scans/{created['scan_id']}").status_code == 404
    with sessions() as session:
        for model in (Repository, ModuleVersion, Scan, Module):
            assert session.scalar(select(func.count()).select_from(model)) == 0
    # Deleting rows never deletes files; the sweep does, once nothing references them.
    assert len(snapshots(settings)) == 1
    assert len(sweep(client, sessions)) == 1 and snapshots(settings) == []
    assert ORIGIN


def test_sweep_keeps_referenced_and_recent_snapshots(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    publish(client, "one")
    publish(client, "two", {**MODULE, "README.md": "# different"})
    orphan = settings.snapshot_root / ("f" * 64 + ".tar.gz")
    orphan.write_bytes(b"left behind by an aborted scan")
    assert len(snapshots(settings)) == 3
    # Too recent: a running scan may be about to reference it.
    assert sweep(client, sessions, grace=3600) == []
    assert sweep(client, sessions) == ["f" * 64]
    assert len(snapshots(settings)) == 2
    assert sweep(client, sessions) == []
    # Two repositories with identical content share one file; it survives until both are gone.
    publish(client, "three")
    assert len(snapshots(settings)) == 2
    client.delete("/api/repositories/one?confirm=one")
    assert sweep(client, sessions) == [] and len(snapshots(settings)) == 2
    client.delete("/api/repositories/three?confirm=three")
    assert len(sweep(client, sessions)) == 1 and len(snapshots(settings)) == 1


def test_running_repository_cannot_be_deleted(stack, sessions):
    client = stack(dispatcher=Mock())
    sign_in(client, sessions)
    register(client, prepare(client)["upload_id"], "busy")
    assert client.delete("/api/repositories/busy").status_code == 409
    assert time and pytest


# ------------------------------------------------------------ explore, stars, forks, profiles


def cards(client, **params):
    response = client.get("/api/repositories", params=params)
    assert response.status_code == 200, response.text
    return response.json()


def names(items):
    return [f"{c['owner']}/{c['name']}" for c in items]


def fork(client, source_owner, source_name, **body):
    return client.post(f"/api/repositories/{source_owner}/{source_name}/fork", json=body)


def as_user(client, sessions, login, github_id):
    return sign_in(client, sessions, login, github_id)


def test_explore_lists_only_public_stored_repositories(stack, sessions):
    parked = stack(dispatcher=Mock())
    as_user(parked, sessions, "alice", 1)
    register(parked, prepare(parked)["upload_id"], "still-running")
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "vpc", description="Virtual network")
    publish(client, "secret", visibility="PRIVATE")
    as_user(client, sessions, "bob", 2)
    publish(client, "eks-cluster", description="Kubernetes with a VPC")
    client.cookies.clear()

    listed = cards(client)
    assert sorted(names(listed)) == ["alice/vpc", "bob/eks-cluster"]
    card = next(c for c in listed if c["name"] == "vpc")
    assert card["description"] == "Virtual network" and card["visibility"] == "PUBLIC"
    assert card["state"] == "PUBLISHED" and card["star_count"] == 0 and card["fork_count"] == 0
    assert card["badge"] == {
        "version": "v1",
        "critical_rules": 1,
        "high_rules": 1,
        "unknown": 1,
        "total": 3,
        "scanned_at": card["badge"]["scanned_at"],
    }
    assert card["badge"]["scanned_at"] and card["forked_from"] is None
    # The owner's own private and running repositories do not appear in explore either.
    as_user(client, sessions, "alice", 1)
    assert sorted(names(cards(client))) == ["alice/vpc", "bob/eks-cluster"]

    assert names(cards(client, search="VPC")) == ["alice/vpc", "bob/eks-cluster"] or sorted(
        names(cards(client, search="VPC"))
    ) == ["alice/vpc", "bob/eks-cluster"]
    assert names(cards(client, search="kubernetes")) == ["bob/eks-cluster"]
    assert cards(client, search="secret") == [] and cards(client, search="%") == []
    assert names(cards(client, sort="name")) == ["bob/eks-cluster", "alice/vpc"]
    assert len(cards(client, limit=1)) == 1 and len(cards(client, limit=1, offset=1)) == 1
    for bad in ({"limit": 51}, {"sort": "random"}, {"offset": -1}):
        assert client.get("/api/repositories", params=bad).status_code == 422


def test_explore_sorts_by_popularity_and_filters_by_provider(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    for name in ("quiet", "liked", "forked"):
        publish(client, name)
    with sessions() as session:
        for name, provider in (("quiet", "aws"), ("liked", "google"), ("forked", "AWS")):
            session.scalar(select(Repository).where(Repository.name == name)).provider = provider
        session.commit()
    as_user(client, sessions, "bob", 2)
    assert client.put("/api/repositories/alice/liked/star").status_code == 200
    assert fork(client, "alice", "forked").status_code == 201
    as_user(client, sessions, "carol", 3)
    client.put("/api/repositories/alice/liked/star")
    client.cookies.clear()
    assert [c["name"] for c in cards(client)] == ["liked", "forked", "quiet"]
    assert sorted(c["name"] for c in cards(client, provider="aws")) == ["forked", "quiet"]
    assert [c["name"] for c in cards(client, provider="GOOGLE")] == ["liked"]
    assert client.get("/api/repository-providers").json() == ["aws", "google"]


def test_stars_are_idempotent_counted_once_and_need_login(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "vpc")
    publish(client, "hidden", visibility="PRIVATE")
    url = "/api/repositories/alice/vpc/star"
    client.cookies.clear()
    assert client.put(url, headers={"Origin": ORIGIN}).status_code == 401
    assert detail(client, "alice", "vpc").json()["starred"] is False

    as_user(client, sessions, "bob", 2)
    assert client.put(url, headers={"Origin": "https://evil.com"}).status_code == 403
    assert client.put(url).json() == {"starred": True, "star_count": 1}
    assert client.put(url).json() == {"starred": True, "star_count": 1}
    assert detail(client, "alice", "vpc").json()["starred"] is True
    # Logins are matched without regard to case.
    assert client.put("/api/repositories/ALICE/vpc/star").json()["star_count"] == 1
    as_user(client, sessions, "carol", 3)
    assert client.put(url).json() == {"starred": True, "star_count": 2}
    assert detail(client, "alice", "vpc").json()["star_count"] == 2
    assert client.delete(url).json() == {"starred": False, "star_count": 1}
    assert client.delete(url).json() == {"starred": False, "star_count": 1}
    # A repository you cannot see cannot be starred, and looks missing.
    assert client.put("/api/repositories/alice/hidden/star").status_code == 404
    assert client.put("/api/repositories/alice/nope/star").status_code == 404
    assert client.put("/api/repositories/nobody/vpc/star").status_code == 404


def test_fork_copies_rows_and_shares_the_snapshot_and_scan(stack, sessions, settings):
    client = stack()
    as_user(client, sessions, "alice", 1)
    created, _ = publish(client, "vpc", description="Network")
    assert fork(client, "alice", "vpc").status_code == 409  # Your own module.
    client.cookies.clear()
    assert fork(client, "alice", "vpc").status_code == 401

    as_user(client, sessions, "bob", 2)
    assert (
        client.post(
            "/api/repositories/alice/vpc/fork", json={}, headers={"Origin": "https://evil.com"}
        ).status_code
        == 403
    )
    response = fork(client, "alice", "vpc")
    assert response.status_code == 201, response.text
    assert response.json() == {
        "owner": "bob",
        "name": "vpc",
        "visibility": "PRIVATE",
        "forked_from": {"owner": "alice", "name": "vpc"},
    }
    mine = detail(client, "bob", "vpc").json()
    assert mine["origin_type"] == "FORK" and mine["visibility"] == "PRIVATE"
    assert mine["forked_from"] == {"owner": "alice", "name": "vpc"} and mine["is_owner"] is True
    assert mine["description"] == "Network" and mine["latest_version"] == "v1"
    assert mine["version"]["readme"] == "# demo" and mine["version"]["state"] == "STORED"
    assert mine["version"]["scan"]["id"] == created["scan_id"]
    assert (mine["version"]["critical_rules"], mine["version"]["high_rules"]) == (1, 1)
    assert detail(client, "alice", "vpc").json()["fork_count"] == 1
    # Nothing was copied or rescanned.
    assert len(snapshots(settings)) == 1
    with sessions() as session:
        assert session.scalar(select(func.count(Scan.id))) == 1
        digests = set(session.scalars(select(ModuleVersion.snapshot_sha256)))
        assert len(digests) == 1 and session.scalar(select(func.count(ModuleVersion.id))) == 2

    # Names: the origin's name, then name-1, name-2; an explicit name must be free.
    assert fork(client, "alice", "vpc").json()["name"] == "vpc-1"
    assert fork(client, "alice", "vpc").json()["name"] == "vpc-2"
    assert fork(client, "alice", "vpc", name="vpc").status_code == 409
    assert fork(client, "alice", "vpc", name="Bad Name").status_code == 422
    public = fork(client, "alice", "vpc", name="my-net", visibility="PUBLIC").json()
    assert public["name"] == "my-net" and public["visibility"] == "PUBLIC"
    assert detail(client, "alice", "vpc").json()["fork_count"] == 4
    # A private fork is invisible to everyone else, including the origin's owner.
    as_user(client, sessions, "alice", 1)
    assert detail(client, "bob", "vpc").status_code == 404
    assert detail(client, "bob", "my-net").status_code == 200
    assert fork(client, "bob", "vpc").status_code == 404


def test_fork_survives_the_origin_being_hidden_or_deleted(stack, sessions, settings):
    client = stack()
    as_user(client, sessions, "alice", 1)
    created, _ = publish(client, "vpc")
    scan_url = f"/api/scans/{created['scan_id']}"
    as_user(client, sessions, "bob", 2)
    assert fork(client, "alice", "vpc").status_code == 201
    (digest,) = snapshots(settings)

    as_user(client, sessions, "alice", 1)
    client.patch("/api/repositories/alice-does-not-matter", json={})
    assert client.patch("/api/repositories/vpc", json={"visibility": "PRIVATE"}).status_code == 204
    as_user(client, sessions, "bob", 2)
    page = detail(client, "bob", "vpc").json()
    # The origin is hidden now, so it is no longer named; the fork itself is intact.
    assert page["forked_from"] is None and page["version"]["readme"] == "# demo"
    assert client.get(scan_url).status_code == 200
    assert len(client.get(scan_url + "/findings").json()) == 3
    as_user(client, sessions, "carol", 3)
    assert client.get(scan_url).status_code == 404
    client.cookies.clear()
    assert client.get(scan_url).status_code == 404

    as_user(client, sessions, "alice", 1)
    assert client.delete("/api/repositories/vpc?confirm=vpc").status_code == 204
    # RETENTION RULE: the fork still references the snapshot, so the sweep must keep it.
    assert sweep(client, sessions) == [] and snapshots(settings) == [digest]
    as_user(client, sessions, "bob", 2)
    page = detail(client, "bob", "vpc").json()
    assert page["version"]["state"] == "STORED" and page["version"]["scan"]["status"] == "COMPLETED"
    assert client.get(scan_url).status_code == 200
    assert len(client.get(scan_url + "/findings").json()) == 3
    with sessions() as session:
        assert session.scalar(select(func.count(Scan.id))) == 1
        assert session.scalar(select(Repository.forked_from_id)) is None
    # A fork can get its own next version like any repository.
    token = prepare(client, {**MODULE, "README.md": "# mine now"})["upload_id"]
    added = client.post("/api/repositories/vpc/versions", json={"upload_id": token})
    assert added.status_code == 201 and added.json()["version"] == "v2"

    # Only when the last referencing version is gone may the file be removed.
    assert (
        client.delete("/api/repositories/vpc?confirm=vpc").status_code == 409
    )  # v2 is still scanning.


def test_snapshot_is_removed_only_after_origin_and_every_fork_are_gone(stack, sessions, settings):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "vpc")
    as_user(client, sessions, "bob", 2)
    fork(client, "alice", "vpc")
    as_user(client, sessions, "carol", 3)
    fork(client, "alice", "vpc")
    (digest,) = snapshots(settings)
    for login, github_id in (("alice", 1), ("bob", 2)):
        as_user(client, sessions, login, github_id)
        assert client.delete("/api/repositories/vpc?confirm=vpc").status_code == 204
        assert sweep(client, sessions) == [] and snapshots(settings) == [digest]
    as_user(client, sessions, "carol", 3)
    assert detail(client, "carol", "vpc").json()["version"]["scan"]["summary"]["total"] == 3
    assert client.delete("/api/repositories/vpc?confirm=vpc").status_code == 204
    assert sweep(client, sessions) == [digest.removesuffix(".tar.gz")]
    assert snapshots(settings) == []
    with sessions() as session:
        for model in (Repository, ModuleVersion, Scan, Module):
            assert session.scalar(select(func.count()).select_from(model)) == 0


def test_deleting_a_fork_gives_the_origin_its_count_back(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    created, _ = publish(client, "vpc")
    as_user(client, sessions, "bob", 2)
    fork(client, "alice", "vpc")
    assert client.delete("/api/repositories/vpc?confirm=vpc").status_code == 204
    page = detail(client, "alice", "vpc").json()
    assert page["fork_count"] == 0 and page["version"]["scan"]["id"] == created["scan_id"]
    assert client.get(f"/api/scans/{created['scan_id']}").status_code == 200


def test_forks_are_rate_limited_per_user(stack, sessions):
    client = stack(forks_per_hour=2, max_repositories_per_user=3)
    as_user(client, sessions, "alice", 1)
    publish(client, "vpc")
    as_user(client, sessions, "bob", 2)
    assert fork(client, "alice", "vpc").status_code == 201
    assert fork(client, "alice", "vpc").status_code == 201
    limited = fork(client, "alice", "vpc")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RateLimitedError"
    # Another user has their own allowance; the repository cap applies separately.
    as_user(client, sessions, "carol", 3)
    assert fork(client, "alice", "vpc").status_code == 201
    capped = stack(forks_per_hour=20, max_repositories_per_user=1)
    as_user(capped, sessions, "dave", 4)
    assert fork(capped, "alice", "vpc").status_code == 201
    assert fork(capped, "alice", "vpc").status_code == 429


def test_profile_shows_each_viewer_only_what_they_may_see(stack, sessions):
    parked = stack(dispatcher=Mock())
    client = stack()
    as_user(client, sessions, "Alice", 1)
    publish(client, "vpc")
    publish(client, "private-net", visibility="PRIVATE")
    as_user(parked, sessions, "Alice", 1)
    register(parked, prepare(parked)["upload_id"], "pending")
    as_user(client, sessions, "bob", 2)
    publish(client, "eks")
    fork(client, "Alice", "vpc")
    client.put("/api/repositories/alice/vpc/star")
    client.put("/api/repositories/bob/eks/star")

    def tab(login, kind):
        response = client.get(f"/api/users/{login}/repositories", params={"kind": kind})
        assert response.status_code == 200, response.text
        return sorted(c["name"] for c in response.json())

    # bob looks at himself: his private fork is there.
    me = client.get("/api/users/BOB").json()
    assert me == {
        "login": "bob",
        "mirror": False,
        "name": None,
        "avatar_url": None,
        "is_self": True,
        "repository_count": 1,
        "fork_count": 1,
        "star_count": 2,
    }
    assert tab("bob", "owned") == ["eks"] and tab("bob", "forked") == ["vpc"]
    assert tab("bob", "starred") == ["eks", "vpc"]
    # bob looks at alice (any letter case): only her public, stored module.
    alice = client.get("/api/users/alice").json()
    assert alice["login"] == "Alice" and alice["is_self"] is False
    assert (alice["repository_count"], alice["fork_count"]) == (1, 0)
    assert tab("ALICE", "owned") == ["vpc"]
    # alice looks at herself: private and pending ones too, with their state.
    as_user(client, sessions, "Alice", 1)
    assert tab("alice", "owned") == ["pending", "private-net", "vpc"]
    own = client.get("/api/users/alice/repositories").json()
    assert {c["name"]: c["state"] for c in own} == {
        "pending": "PENDING",
        "private-net": "PUBLISHED",
        "vpc": "PUBLISHED",
    }
    # She does not see bob's private fork, only that her module was forked once.
    assert tab("bob", "forked") == [] and client.get("/api/users/bob").json()["fork_count"] == 0
    assert next(c for c in own if c["name"] == "vpc")["fork_count"] == 1
    # Anonymous: same as any other stranger; a star on a now-private module disappears.
    client.patch("/api/repositories/vpc", json={"visibility": "PRIVATE"})
    client.cookies.clear()
    assert tab("alice", "owned") == [] and tab("bob", "starred") == ["eks"]
    assert client.get("/api/users/bob").json()["star_count"] == 1
    assert client.get("/api/users/nobody").status_code == 404
    assert client.get("/api/users/nobody/repositories").status_code == 404
    assert client.get("/api/users/bob/repositories", params={"kind": "bad"}).status_code == 422
    assert client.get("/api/users/bob/repositories", params={"limit": 51}).status_code == 422


def test_fake_login_can_sign_in_as_different_users(settings, sessions):
    import httpx
    from fastapi.testclient import TestClient

    from app.core.config import Settings
    from app.main import create_app

    configured = Settings(
        **{
            **settings.model_dump(),
            "_env_file": None,
            "github_client_id": "id",
            "github_client_secret": "secret",
            "session_secret": "session",
            "github_authorize_url": "http://localhost:8000/api/dev/oauth/authorize",
            "github_token_url": "http://localhost:8000/api/dev/oauth/token",
            "github_user_url": "http://localhost:8000/api/dev/oauth/user",
        }
    )
    application = create_app(configured, sessions)
    original = httpx.AsyncClient

    def in_process(**kwargs):
        kwargs.pop("transport", None)
        return original(transport=httpx.ASGITransport(app=application), **kwargs)

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr("app.auth.oauth.httpx.AsyncClient", in_process)
        with TestClient(
            application, base_url="http://localhost:8000", follow_redirects=False
        ) as client:

            def login(cookie):
                if cookie is not None:
                    client.cookies.set("mg_dev_login", cookie, domain="localhost.local", path="/")
                start = client.get("/api/auth/github/login")
                approved = client.get(start.headers["location"])
                client.get(approved.headers["location"])
                return client.get("/api/me").json()["user"]["login"]

            assert login(None) == "dev-user"
            assert login("second-user") == "second-user"
            # Anything that is not a plain login falls back to the default user.
            assert login("../../etc") == "dev-user"
    with sessions() as session:
        from app.models import User

        ids = dict(session.execute(select(User.login, User.github_id)).all())
    assert ids["dev-user"] == 1 and ids["second-user"] != 1


def test_owner_settings_rename_and_confirmed_delete(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    created, _ = publish(client, "old-name", description="First")
    publish(client, "taken")
    # Rename keeps everything else, and the old address stops working.
    assert client.patch("/api/repositories/old-name", json={"name": "new-name"}).status_code == 204
    assert detail(client, "alice", "old-name").status_code == 404
    page = detail(client, "alice", "new-name").json()
    assert page["description"] == "First" and page["version"]["scan"]["id"] == created["scan_id"]
    scan = client.get(f"/api/scans/{created['scan_id']}").json()
    assert scan["repository"]["name"] == "new-name"
    taken = client.patch("/api/repositories/new-name", json={"name": "taken"})
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "ConflictError"
    for bad in ("Bad Name", "-x", "", "a" * 101, "../x"):
        assert client.patch("/api/repositories/new-name", json={"name": bad}).status_code == 422
    # Same name again is a no-op, not a conflict with itself.
    assert client.patch("/api/repositories/new-name", json={"name": "new-name"}).status_code == 204

    # A published repository is deleted only when its name is repeated exactly.
    for confirm in (None, "", "new", "NEW-NAME", "taken"):
        params = {} if confirm is None else {"confirm": confirm}
        refused = client.delete("/api/repositories/new-name", params=params)
        assert refused.status_code == 422, confirm
        assert refused.json()["error"]["code"] == "ConfirmationRequired"
    assert detail(client, "alice", "new-name").status_code == 200
    as_user(client, sessions, "mallory", 2)
    assert client.delete("/api/repositories/new-name?confirm=new-name").status_code == 404
    as_user(client, sessions, "alice", 1)
    assert client.delete("/api/repositories/new-name?confirm=new-name").status_code == 204
    assert detail(client, "alice", "new-name").status_code == 404
    assert detail(client, "alice", "taken").status_code == 200
