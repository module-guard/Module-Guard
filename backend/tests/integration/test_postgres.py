import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

import pytest
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from conftest import FixtureCollector, FixtureScanner
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.engine import make_url
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from alembic import command
from app.core.database import Base
from app.models import Finding, GraphEdge, Module, ParsedEntity, Scan
from app.services.scan_service import ScanService


@pytest.mark.postgres
@pytest.mark.skipif(
    not os.getenv("TEST_DATABASE_URL"), reason="TEST_DATABASE_URL is not configured"
)
def test_postgres_migration_concurrency_and_jsonb(settings, report, monkeypatch):
    base_url = os.environ["TEST_DATABASE_URL"]
    schema = "test_" + uuid4().hex
    admin = create_engine(base_url)
    with admin.begin() as connection:
        connection.execute(text(f'CREATE SCHEMA "{schema}"'))
    url = (
        make_url(base_url)
        .update_query_dict({"options": f"-csearch_path={schema}"})
        .render_as_string(hide_password=False)
    )
    engine = create_engine(url)
    try:
        monkeypatch.setenv("DATABASE_URL", url)
        command.upgrade(Config(str(Path(__file__).parents[2] / "alembic.ini")), "head")
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
        sessions = sessionmaker(engine, expire_on_commit=False)
        service = ScanService(sessions, settings, FixtureCollector(), FixtureScanner(report))
        with ThreadPoolExecutor(max_workers=4) as executor:
            accepted = list(executor.map(service.create, ["https://github.com/org/repo"] * 4))
        service.run(accepted[0].scan_id)
        with sessions() as session:
            assert session.scalar(select(func.count(Module.id))) == 1
            assert session.scalar(select(func.count(Scan.id))) == 4
            scan = session.get(Scan, accepted[0].scan_id)
            assert scan.total_findings == 3
            assert scan.raw_result == report
            assert session.scalar(select(func.count(ParsedEntity.id))) == 2
            assert session.scalar(select(func.count(GraphEdge.id))) == 1
            entity = session.scalar(
                select(ParsedEntity).where(ParsedEntity.entity_type == "RESOURCE")
            )
            assert entity.attributes["name"] == '"bad-example"'
            assert entity.entity_metadata["module_path"] == []
            with pytest.raises(IntegrityError):
                with session.begin_nested():
                    session.add(
                        GraphEdge(
                            scan_id=accepted[1].scan_id,
                            source_entity_id=entity.id,
                            target_address="var.x",
                            edge_type="READS_VARIABLE",
                        )
                    )
                    session.flush()
            assert (
                session.execute(
                    text(
                        "SELECT pg_typeof(raw_result)::text FROM scans WHERE raw_result IS NOT NULL"
                    )
                ).scalar()
                == "jsonb"
            )
        from test_graph_api import GraphCollector

        from app.services.flow_service import FlowService

        flow_service = ScanService(
            sessions, settings, GraphCollector("data_flow/module_input"), FixtureScanner(report)
        )
        flow_scan = flow_service.create("https://github.com/org/repo")
        flow_service.run(flow_scan.scan_id)
        with sessions() as session:
            traced = FlowService(session).trace(
                flow_scan.scan_id, "var.bootstrap", "module.compute.aws_instance.web.user_data"
            )
            assert traced.found and len(traced.paths[0]) == 4
            entity = session.scalar(
                select(ParsedEntity).where(ParsedEntity.scan_id == flow_scan.scan_id)
            )
            session.add(
                GraphEdge(
                    scan_id=flow_scan.scan_id,
                    source_entity_id=None,
                    target_entity_id=entity.id,
                    target_address=entity.address,
                    edge_type="FLOWS_TO",
                    edge_metadata={"test": "unresolved source"},
                )
            )
            session.commit()
            assert (
                session.execute(
                    text("SELECT pg_typeof(metadata)::text FROM graph_edges LIMIT 1")
                ).scalar()
                == "jsonb"
            )
            dependency_ids = set(
                session.scalars(select(GraphEdge.id).where(GraphEdge.edge_type == "READS_VARIABLE"))
            )
        from test_correlation_api import iam_report

        from app.repositories.correlation_repository import CorrelationRepository
        from app.services.correlation_service import CorrelationService

        correlated_service = ScanService(
            sessions,
            settings,
            GraphCollector("correlation/mutable_git_to_iam"),
            FixtureScanner(iam_report()),
        )
        correlated_scan = correlated_service.create("https://github.com/org/repo")
        correlated_service.run(correlated_scan.scan_id)
        with sessions() as session:
            matches = CorrelationRepository(session).list(
                correlated_scan.scan_id,
                20,
                0,
                source="module.policy_source.policy",
                sink="aws_iam_role_policy.admin.policy",
                confidence="MEDIUM",
            )
            assert len(matches) == 1 and matches[0].rule_id == "TFCR001"
            correlation_id = matches[0].id
            evidence = (
                CorrelationService(session).detail(correlated_scan.scan_id, correlation_id).evidence
            )
            assert len(evidence["edges"]) == 2 and not evidence["missing_finding_ids"]
            dependency_ids = set(
                session.scalars(select(GraphEdge.id).where(GraphEdge.edge_type == "READS_VARIABLE"))
            )
        config = Config(str(Path(__file__).parents[2] / "alembic.ini"))
        command.downgrade(config, "0002")
        with engine.connect() as connection:
            assert (
                set(
                    connection.execute(
                        select(GraphEdge.id).where(GraphEdge.edge_type == "READS_VARIABLE")
                    ).scalars()
                )
                == dependency_ids
            )
        command.upgrade(config, "head")
        command.downgrade(config, "0001")
        with engine.connect() as connection:
            assert connection.scalar(select(func.count(Scan.id))) == 6
            assert (
                connection.scalar(select(Finding.rule_id).where(Finding.id == correlation_id))
                == "TFCR001"
            )
        command.upgrade(config, "head")
        with engine.connect() as connection:
            assert compare_metadata(MigrationContext.configure(connection), Base.metadata) == []
    finally:
        engine.dispose()
        with admin.begin() as connection:
            connection.execute(text(f'DROP SCHEMA "{schema}" CASCADE'))
        admin.dispose()
