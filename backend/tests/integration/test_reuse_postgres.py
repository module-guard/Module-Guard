"""Run reuse race and rollback scenarios against a disposable PostgreSQL schema."""

import os
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.config import Config
from sqlalchemy import create_engine, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import sessionmaker

from alembic import command
from tests.integration import test_reuse as cases

material = cases.material
trusted_source = cases.trusted_source
pytestmark = pytest.mark.postgres


@pytest.fixture
def sessions(monkeypatch):
    base = os.environ.get("TEST_DATABASE_URL")
    if not base:
        pytest.skip("TEST_DATABASE_URL is not configured")
    schema = "reuse_" + uuid4().hex
    admin = create_engine(base)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = (
        make_url(base)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    engine = create_engine(url)
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
        yield sessionmaker(engine, expire_on_commit=False)
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()


def test_signed_reuse_and_revocation_pg(stack, sessions, monkeypatch, material):
    cases.test_signed_reuse_and_revocation(stack, sessions, monkeypatch, material)


def test_reuse_concurrent_pg(trusted_source, sessions, monkeypatch):
    cases.test_concurrent_duplicate_target_commits_once(trusted_source, sessions, monkeypatch)


def test_reuse_rollback_pg(trusted_source, sessions, monkeypatch):
    cases.test_copy_failure_rolls_back_results(trusted_source, sessions, monkeypatch)
