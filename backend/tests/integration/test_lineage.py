"""Lineage and trust evidence API (#26).

Covers the four comparison cases the issue names (verbatim copy, single-file change, derivation
of a derivation, not comparable), that hidden or deleted parents and digests leak nothing, the
write-time validation and immutability of records, and the 0011 backfill on SQLite and
PostgreSQL. Integrity (#9) is checked to be unaffected.
"""

import os
from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from conftest import MODULE, prepare, publish, sign_in, wait_terminal
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.models import LineageRecord, Module, ModuleVersion, Repository, Scan, User
from app.models.enums import OriginType, SourceType, Visibility
from app.models.lineage import DECLARATION_VERSION, LineageImmutableError
from app.services.lineage_service import (
    InvalidLineageError,
    compare,
    record_of,
    record_version,
)

CHANGED = {**MODULE, "main.tf": 'resource "null_resource" "changed" {}\n'}
EXTENDED = {**CHANGED, "outputs.tf": 'output "id" {\n  value = "x"\n}\n'}


def url(owner, name, part=""):
    return f"/api/repositories/{owner}/{name}/lineage{part}"


def as_user(client, sessions, login, github_id):
    return sign_in(client, sessions, login, github_id)


def anonymous(client):
    client.cookies.clear()
    return client


def fork(client, owner, name, **body):
    response = client.post(f"/api/repositories/{owner}/{name}/fork", json=body)
    assert response.status_code == 201, response.text
    return response.json()


def new_version(client, name, entries):
    token = prepare(client, entries)["upload_id"]
    response = client.post(f"/api/repositories/{name}/versions", json={"upload_id": token})
    assert response.status_code == 201, response.text
    assert wait_terminal(client, response.json()["scan_id"])["status"] == "COMPLETED"


def set_visibility(client, name, visibility):
    response = client.patch(f"/api/repositories/{name}", json={"visibility": visibility})
    assert response.status_code == 204, response.text


def chain(stack, sessions):
    """alice/net v1 → (alice/net v2) ; alice/net v1 → bob/net v1 → bob/net v2 → carol/net v1."""
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    as_user(client, sessions, "bob", 2)
    fork(client, "alice", "net", visibility="PUBLIC")
    new_version(client, "net", EXTENDED)
    as_user(client, sessions, "carol", 3)
    fork(client, "bob", "net", visibility="PUBLIC")
    return client


# ------------------------------------------------------------- the four comparison cases


def test_verbatim_copy_is_identical_to_its_parent(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    origin = client.get(url("alice", "net")).json()
    bob = as_user(client, sessions, "bob", 2)
    fork(client, "alice", "net", visibility="PUBLIC")

    node = client.get(url("bob", "net")).json()
    assert node["relation"] == "FORK"
    assert node["parent"] == origin["ref"]
    assert node["comparison"]["status"] == "IDENTICAL"
    assert node["comparison"]["unchanged"] == node["file_count"] > 0
    assert node["manifest_sha256"] == origin["manifest_sha256"]
    # Publisher, not author: the stable account id of whoever published, under a declaration.
    assert node["publisher"] == {"id": str(bob), "login": "bob", "system": False}
    assert node["author_asserted"] is False
    assert node["declaration_version"] == DECLARATION_VERSION
    assert node["backfilled"] is False
    assert node["source"]["module_path"] == "."
    diff = client.get(url("bob", "net", "/diff")).json()
    assert diff["files"] == [] and diff["total"] == 0
    assert diff["comparison"]["status"] == "IDENTICAL"

    assert origin["relation"] == "ORIGINAL" and origin["parent"] is None
    assert origin["comparison"] == {
        "status": "NOT_COMPARABLE",
        "reason": "NO_VISIBLE_PARENT",
        "added": 0,
        "removed": 0,
        "modified": 0,
        "unchanged": 0,
    }


def test_single_file_modification_is_reported_per_file(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    new_version(client, "net", CHANGED)

    v1 = client.get(url("alice", "net"), params={"version": "v1"}).json()
    v2 = client.get(url("alice", "net")).json()  # latest by default
    assert v2["ref"]["version"] == "v2"
    assert v2["relation"] == "VERSION" and v2["parent"] == v1["ref"]
    summary = v2["comparison"]
    assert summary["status"] == "CHANGED"
    assert (summary["added"], summary["removed"], summary["modified"]) == (0, 0, 1)
    assert summary["unchanged"] == v1["file_count"] - 1

    diff = client.get(url("alice", "net", "/diff")).json()
    assert diff["total"] == 1 and diff["next_offset"] is None
    [change] = diff["files"]
    assert change["path"] == "main.tf" and change["change"] == "MODIFIED"
    assert change["before"]["sha256"] != change["after"]["sha256"]
    files = {
        f["path"]: f["sha256"]
        for f in client.get("/api/repositories/alice/net/integrity/files").json()
    }
    assert change["after"]["sha256"] == files["main.tf"]


def test_derivation_of_a_derivation(stack, sessions):
    client = chain(stack, sessions)
    carol = client.get(url("carol", "net")).json()
    assert carol["relation"] == "FORK" and carol["comparison"]["status"] == "IDENTICAL"
    assert carol["parent"]["owner"] == "bob" and carol["parent"]["version"] == "v2"

    graph = client.get(url("carol", "net", "/graph")).json()
    assert graph["direction"] == "ancestors" and graph["total"] == 3
    assert [(n["ref"]["owner"], n["ref"]["version"], n["relation"]) for n in graph["nodes"]] == [
        ("bob", "v2", "VERSION"),
        ("bob", "v1", "FORK"),
        ("alice", "v1", "ORIGINAL"),
    ]
    bob_v2 = graph["nodes"][0]["comparison"]
    assert bob_v2["status"] == "CHANGED"
    assert (bob_v2["added"], bob_v2["removed"], bob_v2["modified"]) == (1, 0, 1)
    assert graph["nodes"][1]["comparison"]["status"] == "IDENTICAL"
    ids = [graph["root"]["id"]] + [n["ref"]["id"] for n in graph["nodes"]]
    assert [(e["child"], e["parent"]) for e in graph["edges"]] == list(zip(ids, ids[1:]))
    assert [e["comparison"] for e in graph["edges"]] == ["IDENTICAL", "CHANGED", "IDENTICAL"]

    page = client.get(url("carol", "net", "/graph"), params={"limit": 2}).json()
    assert len(page["nodes"]) == 2 and page["next_offset"] == 2 and page["total"] == 3
    rest = client.get(url("carol", "net", "/graph"), params={"limit": 2, "offset": 2}).json()
    assert [n["ref"]["owner"] for n in rest["nodes"]] == ["alice"] and rest["next_offset"] is None
    assert rest["edges"][0]["child"] == page["nodes"][1]["ref"]["id"]

    down = client.get(url("alice", "net", "/graph"), params={"direction": "descendants"}).json()
    assert [(n["ref"]["owner"], n["ref"]["version"]) for n in down["nodes"]] == [
        ("bob", "v1"),
        ("bob", "v2"),
        ("carol", "v1"),
    ]
    assert down["edges"][0]["parent"] == down["root"]["id"]


def test_a_version_after_a_legacy_one_is_not_comparable_over_the_api(stack, sessions):
    """A version published before #9 has no manifest; the next one says so instead of guessing."""
    client = stack()
    alice = as_user(client, sessions, "alice", 1)
    with sessions() as session:
        repository = Repository(owner_id=alice, name="legacy", origin_type=OriginType.UPLOAD)
        session.add(repository)
        session.flush()
        legacy = ModuleVersion(repository_id=repository.id, version="v1", snapshot_sha256="a" * 64)
        session.add(legacy)
        session.flush()
        record_version(session, legacy, "ORIGINAL", None, alice)
        session.commit()
    new_version(client, "legacy", MODULE)

    node = client.get(url("alice", "legacy")).json()
    assert node["ref"]["version"] == "v2" and node["relation"] == "VERSION"
    assert node["parent"]["version"] == "v1"  # the parent is visible, only not comparable
    assert node["comparison"]["status"] == "NOT_COMPARABLE"
    assert node["comparison"]["reason"] == "MISSING_MANIFEST"
    old = client.get(url("alice", "legacy"), params={"version": "v1"}).json()
    assert old["manifest_sha256"] is None and old["file_count"] is None
    diff = client.get(url("alice", "legacy", "/diff")).json()
    assert diff["files"] == [] and diff["comparison"]["reason"] == "MISSING_MANIFEST"


def test_not_comparable_reasons():
    files = [{"path": "main.tf", "sha256": "a" * 64, "size": 1, "mode": 0o644}]
    current = LineageRecord(manifest={"format": 1, "files": files})
    assert compare(current, None)[0].reason == "NO_VISIBLE_PARENT"
    legacy = LineageRecord(manifest=None)
    assert compare(current, legacy)[0].reason == "MISSING_MANIFEST"
    assert compare(legacy, current)[0].reason == "MISSING_MANIFEST"
    future = LineageRecord(manifest={"format": 2, "files": files})
    summary, changes = compare(current, future)
    assert (summary.status, summary.reason, changes) == ("NOT_COMPARABLE", "FORMAT_MISMATCH", [])
    renamed = LineageRecord(manifest={"format": 1, "files": [{**files[0], "mode": 0o755}]})
    summary, [change] = compare(renamed, current)
    assert summary.status == "CHANGED" and change.change == "MODIFIED"


def test_diff_pagination(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    new_version(client, "net", {f"m{i}.tf": f"# {i}\n" for i in range(5)})
    pages, offset = [], 0
    while offset is not None:
        page = client.get(url("alice", "net", "/diff"), params={"limit": 3, "offset": offset})
        pages.append(page.json())
        offset = pages[-1]["next_offset"]
    summary = pages[0]["comparison"]
    total = summary["added"] + summary["removed"] + summary["modified"]
    assert total > 3 and len(pages) == -(-total // 3)
    assert all(p["total"] == total and len(p["files"]) <= 3 for p in pages)
    paths = [f["path"] for p in pages for f in p["files"]]
    assert paths == sorted(set(paths)) and len(paths) == total
    assert {f["change"] for p in pages for f in p["files"]} == {"ADDED", "REMOVED"}
    assert page.headers["cache-control"] == "no-store"
    assert client.get(url("alice", "net", "/diff"), params={"limit": 51}).status_code == 422


# ------------------------------------------------------------------ nothing hidden leaks


def test_hidden_and_deleted_parents_look_exactly_like_no_parent(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    publish(client, "gone", MODULE)
    as_user(client, sessions, "bob", 2)
    fork(client, "alice", "net", visibility="PUBLIC")
    fork(client, "alice", "gone", visibility="PUBLIC")
    as_user(client, sessions, "alice", 1)
    set_visibility(client, "net", "PRIVATE")
    assert client.delete("/api/repositories/gone?confirm=gone").status_code == 204

    # The owner still sees her own private parent.
    assert client.get(url("bob", "net")).json()["parent"]["owner"] == "alice"

    as_user(client, sessions, "carol", 3)
    hidden = client.get(url("bob", "net")).json()
    deleted = client.get(url("bob", "gone")).json()
    for node in (hidden, deleted):
        assert node["parent"] is None
        assert node["comparison"]["status"] == "NOT_COMPARABLE"
        assert node["comparison"]["reason"] == "NO_VISIBLE_PARENT"
    assert hidden["comparison"] == deleted["comparison"]
    assert {k for k, v in hidden.items() if v != deleted.get(k)} <= {
        "ref",
        "published_at",
        "source",
    }
    for name in ("net", "gone"):
        graph = client.get(url("bob", name, "/graph")).json()
        assert (graph["nodes"], graph["edges"], graph["total"]) == ([], [], 0)
        diff = client.get(url("bob", name, "/diff")).json()
        assert diff["parent"] is None and diff["files"] == []
    assert "alice" not in str(hidden) + str(deleted)


def test_private_repositories_are_indistinguishable_from_missing_ones(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "secret", MODULE, visibility="PRIVATE")
    secret = client.get(url("alice", "secret")).json()
    as_user(client, sessions, "bob", 2)
    for part in ("", "/graph", "/diff"):
        hidden = client.get(url("alice", "secret", part))
        missing = client.get(url("alice", "nothing", part))
        assert hidden.status_code == missing.status_code == 404
        assert hidden.json() == missing.json()
    anonymous(client)
    assert client.get(url("alice", "secret")).json() == client.get(url("alice", "x")).json()
    assert secret["ref"]["name"] == "secret"


def test_digests_cannot_be_used_to_look_anything_up(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "secret", MODULE, visibility="PRIVATE")
    publish(client, "open", CHANGED)
    secret = client.get(url("alice", "secret")).json()
    as_user(client, sessions, "bob", 2)
    for guess in (secret["manifest_sha256"], secret["ref"]["id"]):
        response = client.get(url("alice", "open"), params={"version": guess})
        assert response.status_code == 404
        assert response.json() == client.get(url("alice", "open"), params={"version": "v9"}).json()
    # No lineage operation takes a digest or a record id from the caller.
    spec = client.get("/openapi.json").json()
    lineage_paths = [p for p in spec["paths"] if "/lineage" in p]
    assert lineage_paths
    for path in lineage_paths:
        for operation in spec["paths"][path].values():
            names = {p["name"] for p in operation.get("parameters", [])}
            assert names <= {"owner", "name", "version", "direction", "limit", "offset"}


def test_hidden_descendants_are_skipped_and_not_counted(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    as_user(client, sessions, "bob", 2)
    fork(client, "alice", "net")  # private by default
    as_user(client, sessions, "carol", 3)
    fork(client, "alice", "net", visibility="PUBLIC")

    descendants = {"direction": "descendants"}
    seen_by_carol = client.get(url("alice", "net", "/graph"), params=descendants).json()
    assert [n["ref"]["owner"] for n in seen_by_carol["nodes"]] == ["carol"]
    assert seen_by_carol["total"] == 1 and "bob" not in str(seen_by_carol)
    as_user(client, sessions, "bob", 2)
    seen_by_bob = client.get(url("alice", "net", "/graph"), params=descendants).json()
    assert sorted(n["ref"]["owner"] for n in seen_by_bob["nodes"]) == ["bob", "carol"]


def test_a_hidden_middle_ends_the_walk(stack, sessions):
    client = chain(stack, sessions)
    as_user(client, sessions, "bob", 2)
    set_visibility(client, "net", "PRIVATE")
    anonymous(client)
    carol = client.get(url("carol", "net")).json()
    assert carol["parent"] is None
    graph = client.get(url("carol", "net", "/graph")).json()
    # alice/net is public, but reaching it would reveal the hidden version in between.
    assert graph["nodes"] == [] and graph["total"] == 0
    down = client.get(url("alice", "net", "/graph"), params={"direction": "descendants"}).json()
    assert down["nodes"] == [] and down["total"] == 0


def test_unstored_versions_have_no_visible_lineage(stack, sessions):
    from unittest.mock import Mock

    parked = stack(dispatcher=Mock())
    as_user(parked, sessions, "alice", 1)
    token = prepare(parked)["upload_id"]
    assert (
        parked.post("/api/repositories", json={"upload_id": token, "name": "wip"}).status_code
        == 201
    )
    assert parked.get(url("alice", "wip")).status_code == 404


# ------------------------------------------------------------- write-time rules on records


def make_version(session, owner, name, label="v1"):
    repository = session.scalar(select(Repository).where(Repository.name == name))
    if repository is None:
        repository = Repository(owner_id=owner.id, name=name, origin_type=OriginType.UPLOAD)
        session.add(repository)
        session.flush()
    version = ModuleVersion(repository_id=repository.id, version=label, snapshot_sha256="a" * 64)
    session.add(version)
    session.flush()
    return version


def test_invalid_parents_and_cycles_are_refused(sessions):
    with sessions() as session:
        owner = User(github_id=1, login="alice")
        session.add(owner)
        session.flush()
        a1 = make_version(session, owner, "a")
        root = record_version(session, a1, "ORIGINAL", None, owner.id)
        a2 = make_version(session, owner, "a", "v2")
        b1 = make_version(session, owner, "b")
        refused = [
            (a2, "ORIGINAL", root),  # an original has no parent
            (a2, "VERSION", None),  # a derived version has one
            (b1, "VERSION", root),  # a next version stays in its repository
            (a2, "FORK", root),  # a fork comes from another repository
            (a2, "COPY", root),  # unknown relation
            (a1, "VERSION", root),  # its own parent: a cycle
        ]
        for version, relation, parent in refused:
            with pytest.raises(InvalidLineageError):
                record_version(session, version, relation, parent, owner.id)
        child = record_version(session, a2, "VERSION", root, owner.id)
        with pytest.raises(InvalidLineageError):  # one record per version, ever
            record_version(session, a2, "VERSION", root, owner.id)
        assert child.parent_id == root.id
        assert session.scalar(select(func.count(LineageRecord.id))) == 2


def test_a_corrupted_loop_above_the_parent_is_refused(sessions):
    """Records cannot form a loop through the service; if the data ever does, nothing new is
    attached to it."""
    from sqlalchemy import update

    with sessions() as session:
        owner = User(github_id=1, login="alice")
        session.add(owner)
        session.flush()
        a = record_version(session, make_version(session, owner, "a"), "ORIGINAL", None, owner.id)
        b1 = make_version(session, owner, "b")
        b = record_version(session, b1, "FORK", a, owner.id)
        # Bypasses the ORM guard on purpose, to build the loop a → b → a.
        session.execute(
            update(LineageRecord)
            .where(LineageRecord.id == a.id)
            .values(parent_id=b.id)
            .execution_options(synchronize_session=False)
        )
        session.expire_all()
        b2 = make_version(session, owner, "b", "v2")
        with pytest.raises(InvalidLineageError, match="순환"):
            record_version(session, b2, "VERSION", session.get(LineageRecord, b.id), owner.id)


def test_records_are_immutable_and_survive_deletion(stack, sessions):
    client = stack()
    as_user(client, sessions, "alice", 1)
    publish(client, "net", MODULE)
    as_user(client, sessions, "bob", 2)
    fork(client, "alice", "net", visibility="PUBLIC")
    with sessions() as session:
        record = session.scalar(select(LineageRecord).where(LineageRecord.relation == "FORK"))
        record.manifest_sha256 = "f" * 64
        with pytest.raises(LineageImmutableError):
            session.commit()
        session.rollback()
        session.delete(session.scalar(select(LineageRecord).limit(1)))
        with pytest.raises(LineageImmutableError):
            session.commit()
    assert client.delete("/api/repositories/net?confirm=net").status_code == 204
    with sessions() as session:
        assert session.scalar(select(func.count(LineageRecord.id))) == 2
        assert session.scalar(select(func.count(ModuleVersion.id))) == 1


def test_integrity_is_unchanged_and_agrees_with_lineage(stack, sessions):
    client = chain(stack, sessions)
    for owner, version in (("alice", "v1"), ("bob", "v1"), ("bob", "v2"), ("carol", "v1")):
        base = f"/api/repositories/{owner}/net"
        info = client.get(base + "/integrity", params={"version": version}).json()
        files = client.get(base + "/integrity/files", params={"version": version}).json()
        node = client.get(url(owner, "net"), params={"version": version}).json()
        assert info["integrity_status"] == "VERIFIED"
        assert node["manifest_sha256"] == info["manifest_sha256"]
        assert node["file_count"] == len(files)
    assert client.post("/api/repositories/carol/net/integrity/verify").status_code == 200
    with sessions() as session:
        for record in session.scalars(select(LineageRecord)):
            version = session.get(ModuleVersion, record.version_id)
            assert record.manifest == version.manifest


# ---------------------------------------------------------------------- migration 0011


def backfill_and_check(database_url, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", database_url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0010")
    engine = create_engine(database_url)
    sessions = sessionmaker(engine, expire_on_commit=False)
    start = datetime(2026, 1, 1, tzinfo=UTC)
    manifest = {
        "format": 1,
        "files": [{"path": "main.tf", "sha256": "b" * 64, "size": 1, "mode": 420}],
    }
    with sessions() as session:
        alice, bob = User(github_id=1, login="alice"), User(github_id=2, login="bob")
        module = Module(name="m", source_type=SourceType.UPLOAD, source_url="upload://m")
        session.add_all([alice, bob, module])
        session.flush()
        origin = Repository(
            owner_id=alice.id,
            name="net",
            origin_type=OriginType.GITHUB,
            origin_url="https://github.com/alice/net",
        )
        session.add(origin)
        session.flush()
        copy = Repository(
            owner_id=bob.id,
            name="net",
            origin_type=OriginType.FORK,
            forked_from_id=origin.id,
            visibility=Visibility.PRIVATE,
        )
        session.add(copy)
        session.flush()
        scans = [Scan(module_id=module.id) for _ in range(3)]
        session.add_all(scans)
        session.flush()

        def version(repository, label, minutes, scan, stored=True, with_manifest=True):
            row = ModuleVersion(
                repository_id=repository.id,
                version=label,
                scan_id=scan.id if scan else None,
                snapshot_sha256="a" * 64 if stored else None,
                manifest=manifest if with_manifest else None,
                manifest_sha256="c" * 64 if with_manifest else None,
                created_at=start + timedelta(minutes=minutes),
            )
            session.add(row)
            session.flush()
            return row.id

        ids = {
            "a1": version(origin, "v1", 0, scans[0], with_manifest=False),  # before 0010
            "a2": version(origin, "v2", 1, scans[1]),
            "b1": version(copy, "v1", 2, scans[1]),  # shares a2's scan: a fork of a2
            "b2": version(copy, "v2", 3, scans[2]),
            "b3": version(copy, "v3", 4, None, stored=False),  # never stored
        }
        session.commit()
    command.upgrade(config, "head")
    with sessions() as session:
        records = {r.version_id: r for r in session.scalars(select(LineageRecord))}
        assert set(records) == {ids["a1"], ids["a2"], ids["b1"], ids["b2"]}
        by_version = {key: records.get(value) for key, value in ids.items()}
        assert by_version["a1"].relation == "ORIGINAL" and by_version["a1"].parent_id is None
        assert by_version["a1"].manifest is None
        assert by_version["a1"].source_url == "https://github.com/alice/net"
        assert (by_version["a2"].relation, by_version["a2"].parent_id) == (
            "VERSION",
            by_version["a1"].id,
        )
        assert (by_version["b1"].relation, by_version["b1"].parent_id) == (
            "FORK",
            by_version["a2"].id,
        )
        assert (by_version["b2"].relation, by_version["b2"].parent_id) == (
            "VERSION",
            by_version["b1"].id,
        )
        assert all(r.backfilled and r.declaration_version is None for r in records.values())
        assert (
            by_version["b1"].publisher_id
            == by_version["b2"].publisher_id
            != by_version["a1"].publisher_id
        )
        published = by_version["a2"].published_at
        assert (
            published if published.tzinfo else published.replace(tzinfo=UTC)
        ) == start + timedelta(minutes=1)
        summary, _ = compare(by_version["a2"], by_version["a1"])
        assert (summary.status, summary.reason) == ("NOT_COMPARABLE", "MISSING_MANIFEST")
        assert record_of(session, ids["b3"]) is None
    command.downgrade(config, "0010")
    with engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM module_versions")) == 5
        assert "lineage_records" not in engine.dialect.get_table_names(connection)
    command.upgrade(config, "head")  # and back up again, idempotently
    with sessions() as session:
        assert session.scalar(select(func.count(LineageRecord.id))) == 4
    engine.dispose()


def test_0011_backfills_stored_versions_sqlite(tmp_path, monkeypatch):
    backfill_and_check(f"sqlite:///{tmp_path / 'lineage.sqlite'}", monkeypatch)


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured"
)
def test_0011_backfills_stored_versions_postgres(monkeypatch):
    base_url = os.environ["TEST_DATABASE_URL"]
    schema = "test_" + uuid4().hex
    admin = create_engine(base_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    try:
        backfill_and_check(
            make_url(base_url)
            .update_query_dict({"options": f"-csearch_path={schema}"})
            .render_as_string(hide_password=False),
            monkeypatch,
        )
    finally:
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
