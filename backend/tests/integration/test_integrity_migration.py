from pathlib import Path
from uuid import uuid4

from alembic.config import Config
from sqlalchemy import create_engine, text

from alembic import command


def test_0010_preserves_legacy_and_does_not_invent_baseline(tmp_path, monkeypatch):
    url = f"sqlite:///{tmp_path / 'upgrade.sqlite'}"
    monkeypatch.setenv("DATABASE_URL", url)
    config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
    command.upgrade(config, "0009")
    engine = create_engine(url)
    version_id = uuid4().hex
    with engine.begin() as connection:
        connection.execute(
            text(
                "INSERT INTO module_versions (id, snapshot_sha256, created_at, critical_rules, high_rules) VALUES (:id, :hash, CURRENT_TIMESTAMP, 0, 0)"
            ),
            {"id": version_id, "hash": "a" * 64},
        )
    command.upgrade(config, "0010")
    command.upgrade(config, "head")
    with engine.connect() as connection:
        row = connection.execute(
            text(
                "SELECT snapshot_sha256, manifest, manifest_sha256, integrity_status FROM module_versions WHERE id=:id"
            ),
            {"id": version_id},
        ).one()
        assert tuple(row) == ("a" * 64, None, None, "UNVERIFIED")
    command.downgrade(config, "0009")
    with engine.connect() as connection:
        assert (
            connection.scalar(
                text("SELECT snapshot_sha256 FROM module_versions WHERE id=:id"), {"id": version_id}
            )
            == "a" * 64
        )
    engine.dispose()
