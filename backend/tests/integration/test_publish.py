import tarfile
from unittest.mock import Mock
from uuid import UUID

import pytest
from conftest import (
    ENTROPY,
    ENTROPY_CHECK,
    MODULE,
    ORIGIN,
    TF,
    ZIP,
    prepare,
    publish,
    register,
    sign_in,
    wait_terminal,
    zip_bytes,
)
from sqlalchemy import func, select

from app.models import Finding, Module, ModuleVersion, Repository, Scan
from app.models.enums import SourceType


def test_zip_is_registered_scanned_and_stored_exactly_as_inspected(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    report = prepare(client)
    response = register(client, report["upload_id"], "demo", description="Demo module")
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["owner"] == "alice" and created["name"] == "demo"
    assert created["version"] == "v1" and created["visibility"] == "PUBLIC"
    assert response.headers["location"] == f"/api/scans/{created['scan_id']}"
    scan = wait_terminal(client, created["scan_id"])
    assert scan["status"] == "COMPLETED", scan
    assert scan["repository"] == {
        "owner": "alice",
        "name": "demo",
        "version": "v1",
        "visibility": "PUBLIC",
    }
    assert scan["summary"]["total"] == 3 and scan["commit_hash"] is None
    with sessions() as session:
        repository = session.scalar(select(Repository))
        version = session.scalar(select(ModuleVersion))
        module = session.scalar(select(Module))
        assert repository.description == "Demo module" and repository.origin_type == "UPLOAD"
        assert version.repository_id == repository.id and str(version.scan_id) == created["scan_id"]
        assert version.file_count == 3 and [v["name"] for v in version.variables] == ["name"]
        assert version.readme == "# demo"
        assert module.source_type == SourceType.UPLOAD
        assert module.source_url == f"upload://{repository.id}"
        sha = version.snapshot_sha256
    with tarfile.open(settings.snapshot_root / f"{sha}.tar.gz") as archive:
        # The excluded script never reaches the snapshot.
        assert archive.getnames() == ["README.md", "main.tf", "variables.tf"]
    # The holding area is empty again and the workspace is cleaned.
    assert [p for p in settings.upload_root.rglob("*") if p.is_file()] == []
    assert not list(settings.workspace_root.iterdir())
    mine = client.get("/api/me/repositories").json()
    assert [(m["name"], m["version"], m["latest_scan"]["status"]) for m in mine] == [
        ("demo", "v1", "COMPLETED")
    ]
    assert mine[0]["state"] == "PUBLISHED" and mine[0]["failure"] is None
    assert mine[0]["latest_scan"]["summary"]["total"] == 3
    # The token was spent.
    again = register(client, report["upload_id"], "other")
    assert again.status_code == 404 and again.json()["error"]["code"] == "StagedUploadNotFoundError"


def test_github_source_is_fetched_once_and_later_changes_are_ignored(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    report = client.post("/api/uploads/github", json={"url": "https://github.com/org/demo"}).json()
    # The repository changes after it was inspected.
    client.github.files["main.tf"] = 'resource "null_resource" "changed" {}\n'
    client.github.files["evil.tf"] = "# added later"
    created = register(client, report["upload_id"], report["suggested_name"])
    assert created.status_code == 201, created.text
    scan = wait_terminal(client, created.json()["scan_id"])
    assert scan["status"] == "COMPLETED", scan
    assert client.github.sources == ["https://github.com/org/demo"]
    assert scan["commit_hash"] == "c" * 40 and scan["branch"] == "main"
    assert scan["module"]["source_url"] == "https://github.com/org/demo"
    with sessions() as session:
        version = session.scalar(select(ModuleVersion))
        repository = session.scalar(select(Repository))
        assert repository.origin_type == "GITHUB" and repository.origin_url.endswith("/org/demo")
        assert version.commit_hash == "c" * 40
    with tarfile.open(settings.snapshot_root / f"{version.snapshot_sha256}.tar.gz") as archive:
        assert archive.getnames() == ["README.md", "main.tf", "variables.tf"]
        assert archive.extractfile("main.tf").read().decode() == TF


def test_registration_requires_login_origin_and_the_preparing_user(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    token = prepare(client)["upload_id"]
    body = {"upload_id": token, "name": "demo"}
    assert (
        client.post(
            "/api/repositories", json=body, headers={"Origin": "https://evil.com"}
        ).status_code
        == 403
    )
    sign_in(client, sessions, "mallory", 2)
    stolen = client.post("/api/repositories", json=body)
    assert stolen.status_code == 404
    client.cookies.clear()
    assert client.post("/api/repositories", json=body).status_code == 401
    assert client.get("/api/me/repositories").status_code == 401
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 0
    # The rightful owner can still use it.
    sign_in(client, sessions)
    assert client.post("/api/repositories", json=body).status_code == 201


def test_expired_preparation_cannot_be_registered(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    token = prepare(client)["upload_id"]
    client.publish.staging.ttl_seconds = -1
    token = prepare(client)["upload_id"]
    response = register(client, token)
    assert response.status_code == 404 and "만료" in response.json()["error"]["message"]


def test_entropy_warnings_must_be_acknowledged(stack, sessions):
    client = stack(checks=[ENTROPY_CHECK])
    sign_in(client, sessions)
    report = prepare(client, {"main.tf": f'digest = "{ENTROPY}"\n' + TF})
    assert len(report["warnings"]) == 1
    refused = register(client, report["upload_id"])
    assert refused.status_code == 422
    assert refused.json()["error"]["code"] == "WarningsNotAcknowledged"
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 0
    # Refusal does not spend the token.
    accepted = register(client, report["upload_id"], acknowledge_warnings=True)
    assert accepted.status_code == 201
    assert wait_terminal(client, accepted.json()["scan_id"])["status"] == "COMPLETED"


@pytest.mark.parametrize(
    "name", ["Demo", "my module", "-demo", "_demo", "a" * 101, "", "데모", "a/b", "../x", "a.b"]
)
def test_invalid_names_are_rejected(stack, sessions, name):
    client = stack()
    sign_in(client, sessions)
    assert register(client, prepare(client)["upload_id"], name).status_code == 422


def test_names_are_unique_per_owner(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    publish(client, "demo")
    taken = register(client, prepare(client)["upload_id"], "demo")
    assert taken.status_code == 409 and taken.json()["error"]["code"] == "ConflictError"
    # Someone else may use the same name.
    sign_in(client, sessions, "bob", 2)
    _, scan = publish(client, "demo")
    assert scan["repository"]["owner"] == "bob"


def test_failed_first_version_frees_the_name_and_leaves_no_trace(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    client.scans.snapshot_store.save = Mock(side_effect=OSError("disk full"))
    created, failed = publish(client, "demo", visibility="PRIVATE")
    assert failed["status"] == "FAILED"
    assert failed["error_message"] == "Source snapshot could not be stored."
    mine = client.get("/api/me/repositories").json()
    assert mine[0]["latest_scan"]["status"] == "FAILED" and mine[0]["state"] == "FAILED"
    assert mine[0]["failure"] == "Source snapshot could not be stored."
    del client.scans.snapshot_store.save
    _, scan = publish(client, "demo", visibility="PRIVATE")
    assert scan["status"] == "COMPLETED"
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 1
        assert session.scalar(select(func.count(Scan.id))) == 1
        assert session.scalar(select(func.count(Module.id))) == 1
        assert session.get(Scan, UUID(created["scan_id"])) is None


def test_running_publishes_are_limited_per_user(stack, sessions):
    parked = Mock()  # Accepts submissions but never runs them.
    client = stack(dispatcher=parked, max_running_publishes=1)
    sign_in(client, sessions)
    assert register(client, prepare(client)["upload_id"], "one").status_code == 201
    limited = register(client, prepare(client)["upload_id"], "two")
    assert limited.status_code == 429 and limited.json()["error"]["code"] == "RateLimitedError"
    assert parked.submit.call_count == 1


def test_scan_fails_rather_than_refetching_when_the_prepared_source_is_gone(stack, sessions):
    parked = Mock()
    client = stack(dispatcher=parked)
    sign_in(client, sessions)
    report = client.post("/api/uploads/github", json={"url": "https://github.com/org/demo"}).json()
    created = register(client, report["upload_id"], "demo").json()
    client.publish.staging.release(UUID(created["scan_id"]))
    client.scans.run(UUID(created["scan_id"]))
    scan = client.get(f"/api/scans/{created['scan_id']}").json()
    assert scan["status"] == "FAILED"
    assert scan["error_message"] == "Prepared source is no longer available."
    assert client.github.sources == ["https://github.com/org/demo"]


SUB_ROUTES = (
    "",
    "/findings",
    "/entities",
    "/graph",
    "/dependencies",
    "/flows",
    "/flows/aws_security_group.bad_example",
    "/flow-trace?source=a&target=b",
    "/correlations",
)


def test_private_repository_scans_are_visible_only_to_the_owner(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    private, _ = publish(client, "secret-infra", visibility="PRIVATE")
    public, _ = publish(client, "open-infra")
    hidden, shown = private["scan_id"], public["scan_id"]

    def statuses(scan_id):
        return {
            route: client.get(f"/api/scans/{scan_id}{route}").status_code for route in SUB_ROUTES
        }

    def listed():
        profile = {c["name"] for c in client.get("/api/users/alice/repositories").json()}
        explore = {c["name"] for c in client.get("/api/repositories").json()}
        return profile, explore

    owner_view = statuses(hidden)
    assert owner_view[""] == 200 and owner_view["/findings"] == 200
    assert all(code != 404 or route.startswith("/flow") for route, code in owner_view.items())
    assert listed() == ({"secret-infra", "open-infra"}, {"open-infra"})
    with sessions() as session:
        finding_id = session.scalar(
            select(Finding.id).where(Finding.scan_id == UUID(hidden)).limit(1)
        )

    for viewer in ("anonymous", "other"):
        if viewer == "anonymous":
            client.cookies.clear()
        else:
            sign_in(client, sessions, "mallory", 2)
        assert set(statuses(hidden).values()) == {404}, viewer
        assert client.get(f"/api/scans/{hidden}/correlations/{finding_id}").status_code == 404
        assert statuses(shown)[""] == 200
        assert listed() == ({"open-infra"}, {"open-infra"})


def test_queue_refusal_marks_the_scan_failed(stack, sessions):
    from app.core.exceptions import ScanQueueFullError

    full = Mock()
    full.submit.side_effect = ScanQueueFullError("Scan queue is full. Retry later.")
    client = stack(dispatcher=full)
    sign_in(client, sessions)
    response = register(client, prepare(client)["upload_id"], "demo")
    assert response.status_code == ScanQueueFullError.status_code
    with sessions() as session:
        scan = session.scalar(select(Scan))
        assert scan.status == "FAILED"
    assert [p for p in client.publish.staging.root.rglob("*") if p.is_file()] == []
    assert ORIGIN


def test_failed_and_running_public_attempts_are_hidden_from_everyone_else(stack, sessions):
    parked = Mock()
    client = stack(dispatcher=parked)
    owner = sign_in(client, sessions)
    running = register(client, prepare(client)["upload_id"], "in-progress").json()
    assert client.get("/api/me/repositories").json()[0]["state"] == "PENDING"

    def others_see(scan_id):
        seen = []
        for viewer in ("anonymous", "other"):
            if viewer == "anonymous":
                client.cookies.clear()
            else:
                sign_in(client, sessions, "mallory", 2)
            profile = {c["name"] for c in client.get("/api/users/alice/repositories").json()}
            explore = {c["name"] for c in client.get("/api/repositories").json()}
            seen.append((client.get(f"/api/scans/{scan_id}").status_code, profile, explore))
        sign_in(client, sessions)
        return seen

    assert others_see(running["scan_id"]) == [(404, set(), set())] * 2
    assert client.get(f"/api/scans/{running['scan_id']}").status_code == 200
    # The attempt fails: still only the owner sees it.
    client.scans.snapshot_store.save = Mock(side_effect=OSError("disk full"))
    client.scans.run(UUID(running["scan_id"]))
    assert client.get(f"/api/scans/{running['scan_id']}").json()["status"] == "FAILED"
    assert others_see(running["scan_id"]) == [(404, set(), set())] * 2
    assert owner


def test_owner_can_delete_a_failed_attempt(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    client.scans.snapshot_store.save = Mock(side_effect=OSError("disk full"))
    created, failed = publish(client, "broken")
    assert failed["status"] == "FAILED"
    del client.scans.snapshot_store.save
    publish(client, "good")

    assert client.delete("/api/repositories/broken", headers={"Origin": ""}).status_code == 403
    sign_in(client, sessions, "mallory", 2)
    assert client.delete("/api/repositories/broken").status_code == 404
    client.cookies.clear()
    assert client.delete("/api/repositories/broken").status_code == 401
    sign_in(client, sessions)
    assert client.delete("/api/repositories/missing").status_code == 404

    assert client.delete("/api/repositories/broken").status_code == 204
    assert [m["name"] for m in client.get("/api/me/repositories").json()] == ["good"]
    assert client.get(f"/api/scans/{created['scan_id']}").status_code == 404
    with sessions() as session:
        assert session.scalar(select(func.count(Repository.id))) == 1
        assert session.scalar(select(func.count(Scan.id))) == 1
        assert session.scalar(select(func.count(Module.id))) == 1
    assert client.delete("/api/repositories/broken").status_code == 404


def test_running_attempt_cannot_be_deleted(stack, sessions):
    client = stack(dispatcher=Mock())
    sign_in(client, sessions)
    register(client, prepare(client)["upload_id"], "busy")
    busy = client.delete("/api/repositories/busy")
    assert busy.status_code == 409 and "진행 중" in busy.json()["error"]["message"]


# ---------------------------------------------------------------- repository page and versions


def new_version(client, name, entries=None, **extra):
    token = prepare(client, entries)["upload_id"]
    return client.post(f"/api/repositories/{name}/versions", json={"upload_id": token, **extra})


def detail(client, owner="alice", name="demo", **params):
    return client.get(f"/api/repositories/{owner}/{name}", params=params)


def test_repository_detail_shows_the_latest_stored_version(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    publish(client, "demo", {**MODULE, "LICENSE": "Apache License\nVersion 2.0, January 2004\n"})
    page = detail(client).json()
    assert page["owner"] == "alice" and page["name"] == "demo" and page["is_owner"] is True
    assert page["visibility"] == "PUBLIC" and page["origin_type"] == "UPLOAD"
    assert page["latest_version"] == "v1" and [v["version"] for v in page["versions"]] == ["v1"]
    version = page["version"]
    assert version["version"] == "v1" and version["state"] == "STORED"
    assert version["readme"] == "# demo" and version["license"] == "Apache-2.0"
    assert [v["name"] for v in version["variables"]] == ["name"] and version["outputs"] == []
    assert version["file_count"] == 4 and version["scan"]["status"] == "COMPLETED"
    assert version["scan"]["summary"]["total"] == 3
    # The badge counts distinct rules, not occurrences.
    assert (version["critical_rules"], version["high_rules"]) == (1, 1)
    assert version["scanner_breakdown"] == {"checkov": 3}
    # Others see the same public page, without owner controls. Logins match case-insensitively.
    client.cookies.clear()
    public = detail(client, "ALICE").json()
    assert public["is_owner"] is False and public["version"]["readme"] == "# demo"
    assert detail(client, "alice", "missing").status_code == 404
    assert detail(client, "nobody", "demo").status_code == 404
    assert detail(client, version="v9").status_code == 404


def test_rule_kinds_count_distinct_rules(stack, sessions, report):
    report["results"]["failed_checks"] += [
        {**report["results"]["failed_checks"][0], "resource": f"aws_security_group.extra{i}"}
        for i in range(4)
    ]
    report["summary"]["failed"] = 7
    client = stack()
    sign_in(client, sessions)
    publish(client, "demo")
    version = detail(client).json()["version"]
    assert version["scan"]["summary"]["high"] == 5 and version["high_rules"] == 1


def test_private_and_unpublished_repositories_are_owner_only(stack, sessions):
    parked = Mock()
    client = stack(dispatcher=parked)
    sign_in(client, sessions)
    register(client, prepare(client)["upload_id"], "pending")
    owner_view = detail(client, name="pending").json()
    assert owner_view["latest_version"] is None and owner_view["version"]["state"] == "PENDING"
    client.cookies.clear()
    assert detail(client, name="pending").status_code == 404

    private = stack()
    sign_in(private, sessions)
    publish(private, "hidden", visibility="PRIVATE")
    assert detail(private, name="hidden").status_code == 200
    for viewer in ("anonymous", "other"):
        if viewer == "anonymous":
            private.cookies.clear()
        else:
            sign_in(private, sessions, "mallory", 2)
        response = detail(private, name="hidden")
        assert response.status_code == 404
        assert response.json()["error"]["message"] == "Repository not found."


def test_new_versions_are_numbered_and_become_latest(stack, sessions, settings):
    client = stack()
    sign_in(client, sessions)
    publish(client, "demo")
    response = new_version(client, "demo", {**MODULE, "README.md": "# second"})
    assert response.status_code == 201, response.text
    created = response.json()
    assert created["version"] == "v2" and created["name"] == "demo"
    assert response.headers["location"] == f"/api/scans/{created['scan_id']}"
    scan = wait_terminal(client, created["scan_id"])
    assert scan["status"] == "COMPLETED" and scan["repository"]["version"] == "v2"
    page = detail(client).json()
    assert page["latest_version"] == "v2" and page["version"]["readme"] == "# second"
    assert [(v["version"], v["state"]) for v in page["versions"]] == [
        ("v2", "STORED"),
        ("v1", "STORED"),
    ]
    assert detail(client, version="v1").json()["version"]["readme"] == "# demo"
    assert new_version(client, "demo").json()["version"] == "v3"
    with sessions() as session:
        # Uploaded versions share one internal module row.
        assert session.scalar(select(func.count(Module.id))) == 1
        assert session.scalar(select(func.count(Repository.id))) == 1


def test_failed_new_version_keeps_the_previous_one_and_frees_its_number(stack, sessions):
    client = stack()
    sign_in(client, sessions)
    publish(client, "demo")
    client.scans.snapshot_store.save = Mock(side_effect=OSError("disk full"))
    failed = new_version(client, "demo", {**MODULE, "README.md": "# broken"}).json()
    assert failed["version"] == "v2"
    assert wait_terminal(client, failed["scan_id"])["status"] == "FAILED"
    page = detail(client).json()
    assert page["latest_version"] == "v1" and page["version"]["readme"] == "# demo"
    assert [(v["version"], v["state"]) for v in page["versions"]] == [
        ("v2", "FAILED"),
        ("v1", "STORED"),
    ]
    assert page["versions"][0]["failure"] == "Source snapshot could not be stored."
    mine = client.get("/api/me/repositories").json()
    assert mine[0]["state"] == "PUBLISHED"
    # Other people never see the failed attempt.
    client.cookies.clear()
    public = detail(client).json()
    assert [v["version"] for v in public["versions"]] == ["v1"]
    assert detail(client, version="v2").status_code == 404
    assert client.get(f"/api/scans/{failed['scan_id']}").status_code == 200  # v1 is published
    sign_in(client, sessions)
    del client.scans.snapshot_store.save
    retried = new_version(client, "demo").json()
    assert retried["version"] == "v2"
    assert wait_terminal(client, retried["scan_id"])["status"] == "COMPLETED"
    assert client.get(f"/api/scans/{failed['scan_id']}").status_code == 404
    assert [v["state"] for v in detail(client).json()["versions"]] == ["STORED", "STORED"]


def test_new_version_rules(stack, sessions):
    parked = Mock()
    client = stack(checks=[ENTROPY_CHECK], dispatcher=parked)
    sign_in(client, sessions)
    token = prepare(client)["upload_id"]
    assert register(client, token, "demo", acknowledge_warnings=True).status_code == 201
    busy = new_version(client, "demo", acknowledge_warnings=True)
    assert busy.status_code == 409 and "진행 중" in busy.json()["error"]["message"]
    scan_id = client.get("/api/me/repositories").json()[0]["latest_scan"]["id"]
    client.scans.run(UUID(scan_id))

    assert new_version(client, "missing", acknowledge_warnings=True).status_code == 404
    sign_in(client, sessions, "mallory", 2)
    assert new_version(client, "demo", acknowledge_warnings=True).status_code == 404  # Not hers.
    client.cookies.clear()
    anonymous = client.post("/api/repositories/demo/versions", json={"upload_id": "x" * 32})
    assert anonymous.status_code == 401
    sign_in(client, sessions)
    body = {"upload_id": prepare(client)["upload_id"]}
    no_origin = client.post(
        "/api/repositories/demo/versions", json=body, headers={"Origin": "https://evil.com"}
    )
    assert no_origin.status_code == 403
    # Secret rules apply to new versions exactly as to the first one.
    warned = new_version(client, "demo")
    assert warned.status_code == 422
    assert warned.json()["error"]["code"] == "WarningsNotAcknowledged"
    leaked = client.post(
        "/api/uploads/zip",
        content=zip_bytes({"main.tf": 'key = "' + "AKIA" + "Z7QHB3K2" + "LMNOP4RT" + '"\n'}),
        headers=ZIP,
    )
    assert leaked.status_code == 422 and leaked.json()["error"]["code"] == "SecretsDetected"
    with sessions() as session:
        assert session.scalar(select(func.count(ModuleVersion.id))) == 1
