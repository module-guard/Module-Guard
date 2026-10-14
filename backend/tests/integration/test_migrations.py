from pathlib import Path

from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect

from alembic import command
from app.core.database import Base


def test_migration_matches_models(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'migration.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "head")
    engine = create_engine(url)
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    command.downgrade(config, "base")
    assert "scans" not in inspect(engine).get_table_names()
    engine.dispose()


def test_v2_upgrade_and_downgrade_preserve_mvp_rows(tmp_path, monkeypatch):
    from uuid import uuid4

    from sqlalchemy import select, text

    from app.models import Finding, Module, Scan
    from app.models.enums import SourceType

    url = f"sqlite:///{tmp_path / 'legacy.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0001")
    engine = create_engine(url)
    module_id, scan_id, finding_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            Module.__table__.insert().values(
                id=module_id,
                name="legacy",
                source_type=SourceType.GITHUB,
                source_url="https://github.com/org/legacy",
            )
        )
        connection.execute(Scan.__table__.insert().values(id=scan_id, module_id=module_id))
        # Raw SQL: the current model has columns this old schema does not know yet.
        connection.execute(
            text(
                "INSERT INTO findings (id, scan_id, rule_id, scanner, severity, title,"
                " check_result, raw_data, created_at) VALUES (:id, :scan, 'OLD', 'checkov',"
                " 'HIGH', 'Old finding', 'FAILED', '{}', '2026-01-01')"
            ),
            {"id": finding_id.hex, "scan": scan_id.hex},
        )
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert connection.execute(select(Scan.analysis).where(Scan.id == scan_id)).scalar() is None
        assert (
            connection.execute(select(Finding.title).where(Finding.id == finding_id)).scalar()
            == "Old finding"
        )
    command.downgrade(config, "0001")
    assert "parsed_entities" not in inspect(engine).get_table_names()
    with engine.connect() as connection:
        assert connection.execute(select(Scan.id).where(Scan.id == scan_id)).scalar() == scan_id
        assert (
            connection.execute(select(Finding.title).where(Finding.id == finding_id)).scalar()
            == "Old finding"
        )
    command.upgrade(config, "head")
    engine.dispose()


def test_v3_nullable_source_and_downgrade_preserves_dependency(tmp_path, monkeypatch):
    from uuid import uuid4

    from sqlalchemy import select

    from app.models import GraphEdge, Module, ParsedEntity, Scan

    url = f"sqlite:///{tmp_path / 'flow.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0002")
    engine = create_engine(url)
    module_id, scan_id, entity_id, dependency_id = [uuid4() for _ in range(4)]
    with engine.begin() as connection:
        connection.execute(
            Module.__table__.insert().values(
                id=module_id,
                name="legacy",
                source_type="GITHUB",
                source_url="https://github.com/org/legacy",
            )
        )
        connection.execute(Scan.__table__.insert().values(id=scan_id, module_id=module_id))
        connection.execute(
            ParsedEntity.__table__.insert().values(
                id=entity_id,
                scan_id=scan_id,
                entity_type="RESOURCE",
                address="aws_instance.web",
                name="web",
                file_path="main.tf",
            )
        )
        connection.execute(
            GraphEdge.__table__.insert().values(
                id=dependency_id,
                scan_id=scan_id,
                source_entity_id=entity_id,
                edge_type="REFERENCES",
                target_address="var.x",
            )
        )
    command.upgrade(config, "head")
    with engine.begin() as connection:
        connection.execute(
            GraphEdge.__table__.insert().values(
                scan_id=scan_id,
                source_entity_id=None,
                target_entity_id=entity_id,
                edge_type="SINKS_TO_RESOURCE_ATTRIBUTE",
                target_address="aws_instance.web.user_data",
            )
        )
    command.downgrade(config, "0002")
    with engine.connect() as connection:
        assert connection.execute(select(GraphEdge.id)).scalars().all() == [dependency_id]
        assert connection.execute(select(ParsedEntity.id)).scalar() == entity_id
    command.upgrade(config, "head")
    with engine.connect() as connection:
        assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    engine.dispose()


def test_v4_repository_layer_preserves_scan_rows(tmp_path, monkeypatch):
    from uuid import uuid4

    from sqlalchemy import select, text

    from app.models import Finding, Module, Scan
    from app.models.enums import Severity, SourceType

    url = f"sqlite:///{tmp_path / 'repositories.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0003")
    engine = create_engine(url)
    module_id, scan_id, finding_id = uuid4(), uuid4(), uuid4()
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO modules (id, name, source_type, source_url, created_at, updated_at)"
                " VALUES (:id, 'legacy', 'GITHUB', 'https://github.com/org/legacy',"
                " '2026-01-01', '2026-01-01')"
            ),
            {"id": module_id.hex},
        )
        connection.execute(
            text(
                "INSERT INTO scans (id, module_id, status, scanner, total_findings, critical_count,"
                " high_count, medium_count, low_count, info_count, unknown_count, partial_result,"
                " warnings, created_at) VALUES (:id, :module, 'COMPLETED', 'checkov', 1, 0, 1, 0,"
                " 0, 0, 0, 0, '[]', '2026-01-01')"
            ),
            {"id": scan_id.hex, "module": module_id.hex},
        )
        connection.execute(
            text(
                "INSERT INTO findings (id, scan_id, rule_id, scanner, severity, title,"
                " check_result, raw_data, created_at) VALUES (:id, :scan, 'OLD', 'checkov',"
                " 'HIGH', 'Old finding', 'FAILED', '{}', '2026-01-01')"
            ),
            {"id": finding_id.hex, "scan": scan_id.hex},
        )

    def assert_preserved():
        with engine.connect() as connection:
            assert connection.execute(select(Module.name)).scalar() == "legacy"
            assert connection.execute(select(Scan.high_count)).scalar() == 1
            assert connection.execute(select(Finding.severity)).scalar() == Severity.HIGH
            assert connection.execute(select(Module.source_type)).scalar() == SourceType.GITHUB

    command.upgrade(config, "head")
    tables = set(inspect(engine).get_table_names())
    assert {"users", "repositories", "module_versions", "stars", "sessions"} <= tables
    assert_preserved()
    with engine.connect() as connection:
        assert connection.execute(select(Scan.module_version_id)).scalar() is None
    command.downgrade(config, "0003")
    tables = set(inspect(engine).get_table_names())
    assert not {"users", "repositories", "module_versions", "stars", "sessions"} & tables
    assert "module_version_id" not in {c["name"] for c in inspect(engine).get_columns("scans")}
    with engine.connect() as connection:
        assert connection.execute(text("SELECT title FROM findings")).scalar() == "Old finding"
        assert connection.execute(text("SELECT high_count FROM scans")).scalar() == 1
    command.upgrade(config, "head")
    assert_preserved()
    engine.dispose()


def test_v6_upload_source_type_is_enforced_both_ways(tmp_path, monkeypatch):
    from uuid import uuid4

    import pytest
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    url = f"sqlite:///{tmp_path / 'upload.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    engine = create_engine(url)
    insert = text(
        "INSERT INTO modules (id, name, source_type, source_url, created_at, updated_at)"
        " VALUES (:id, 'm', :kind, :url, '2026-01-01', '2026-01-01')"
    )

    def add(kind, source):
        with engine.begin() as connection:
            connection.execute(insert, {"id": uuid4().hex, "kind": kind, "url": source})

    command.upgrade(config, "0005")
    add("GITHUB", "https://github.com/org/kept")
    with pytest.raises(IntegrityError):
        add("UPLOAD", "upload://early")
    command.upgrade(config, "head")
    add("UPLOAD", "upload://one")
    with pytest.raises(IntegrityError):
        add("BOGUS", "x")
    command.downgrade(config, "0005")
    with engine.connect() as connection:
        rows = connection.execute(text("SELECT source_type FROM modules")).scalars().all()
    # Uploaded modules cannot exist before 0006; everything else survives.
    assert rows == ["GITHUB"]
    with pytest.raises(IntegrityError):
        add("UPLOAD", "upload://late")
    engine.dispose()


def test_v9_system_accounts_survive_upgrade_and_downgrade(tmp_path, monkeypatch):
    from sqlalchemy import text

    url = f"sqlite:///{tmp_path / 'migration.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0008")
    engine = create_engine(url)
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO users (id, github_id, login, created_at)"
                " VALUES ('11111111111111111111111111111111', 7, 'alice', '2026-01-01')"
            )
        )
    command.upgrade(config, "0009")
    with engine.begin() as connection:
        assert connection.execute(text("SELECT is_system, github_id FROM users")).all() == [(0, 7)]
        connection.execute(
            text(
                "INSERT INTO users (id, github_id, login, created_at, is_system)"
                " VALUES ('22222222222222222222222222222222', NULL, 'mirror', '2026-01-01', 1)"
            )
        )
    command.downgrade(config, "0008")
    with engine.connect() as connection:
        rows = dict(connection.execute(text("SELECT login, github_id FROM users")).all())
    assert rows == {"alice": 7, "mirror": -1}
    engine.dispose()
