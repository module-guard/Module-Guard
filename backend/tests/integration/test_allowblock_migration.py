from datetime import UTC, datetime, timedelta
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from alembic import command

NOW = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.fixture
def migrated(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'allowblock.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    yield config, engine
    engine.dispose()


def entry(**over):
    row = {
        "id": uuid4().hex,
        "target_kind": "github_owner",
        "target_key": "evil",
        "effect": "BLOCK",
        "reason": "malware",
        "evidence_urls": "[]",
        "created_at": NOW.isoformat(),
        "expires_at": None,
    }
    row.update(over)
    return row


INSERT = text(
    "INSERT INTO allowblock_entries (id, target_kind, target_key, effect, reason,"
    " evidence_urls, created_at, expires_at, revoked_at) VALUES (:id, :target_kind,"
    " :target_key, :effect, :reason, :evidence_urls, :created_at, :expires_at, :revoked_at)"
)


def insert(connection, **over):
    row = entry(**over)
    row.setdefault("revoked_at", None)
    connection.execute(INSERT, row)


def test_head_creates_both_tables_and_downgrade_removes_them(migrated):
    config, engine = migrated
    tables = inspect(engine).get_table_names()
    assert {"allowblock_entries", "allowblock_events"} <= set(tables)
    command.downgrade(config, "0013")
    tables = inspect(engine).get_table_names()
    assert "allowblock_entries" not in tables
    assert "allowblock_events" not in tables
    assert "reputation_snapshots" in tables
    command.upgrade(config, "head")
    assert "allowblock_events" in inspect(engine).get_table_names()


def test_revision_chain(migrated):
    from alembic.script import ScriptDirectory

    config, _ = migrated
    script = ScriptDirectory.from_config(config)
    assert script.get_revision("0014").down_revision == "0013"
    assert script.get_current_head() == "0014"


def test_only_one_unrevoked_entry_per_target_and_effect(migrated):
    _, engine = migrated
    with engine.begin() as connection:
        insert(connection)
    with pytest.raises(IntegrityError), engine.begin() as connection:
        insert(connection)
    with engine.begin() as connection:
        insert(connection, effect="ALLOW")  # another effect
        insert(connection, target_key="other")  # another target
        insert(connection, revoked_at=NOW.isoformat())  # revoked rows do not count
        insert(connection, revoked_at=NOW.isoformat())


def test_check_constraints(migrated):
    _, engine = migrated
    for bad in (
        {"effect": "MAYBE"},
        {"target_kind": "glob"},
        {"expires_at": (NOW - timedelta(days=1)).isoformat()},
        {"expires_at": NOW.isoformat()},
    ):
        with pytest.raises(IntegrityError), engine.begin() as connection:
            insert(connection, **bad)
    with engine.begin() as connection:
        insert(connection, expires_at=(NOW + timedelta(days=1)).isoformat())


def test_event_action_is_constrained(migrated):
    _, engine = migrated
    with engine.begin() as connection:
        insert(connection, id="e1")
        for action, ok in (("CREATE", True), ("REVOKE", True), ("EXPIRE", True), ("EDIT", False)):
            statement = text(
                "INSERT INTO allowblock_events (id, entry_id, action, actor_login, occurred_at,"
                " reason, evidence_urls, snapshot) VALUES (:id, 'e1', :action, 'admin', :at,"
                " 'r', '[]', '{}')"
            )
            params = {"id": uuid4().hex, "action": action, "at": NOW.isoformat()}
            if ok:
                connection.execute(statement, params)
            else:
                with pytest.raises(IntegrityError), connection.begin_nested():
                    connection.execute(statement, params)
