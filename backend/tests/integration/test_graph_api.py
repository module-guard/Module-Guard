import shutil
from unittest.mock import Mock
from uuid import uuid4

import pytest
from conftest import FIXTURES, FixtureCollector, FixtureScanner
from fastapi.testclient import TestClient
from sqlalchemy import func, select

from app.core.exceptions import ScanTimeoutError
from app.main import create_app
from app.models import Finding, GraphEdge, ParsedEntity, Scan
from app.parsers.domain import ParseDiagnostic
from app.parsers.hcl import HCLTerraformParser
from app.services.analysis_service import StaticAnalysisService
from app.services.scan_service import ScanService


class GraphCollector(FixtureCollector):
    def __init__(self, fixture="dependency_graph/basic"):
        self.fixture = fixture

    def collect(self, source, destination):
        result = super().collect(source, destination)
        # Preserve the independently created workspace, replace only this test's fixture content.
        (destination / "main.tf").unlink()
        shutil.copytree(FIXTURES / self.fixture, destination, dirs_exist_ok=True)
        return result


def service_for(sessions, settings, report, fixture="dependency_graph/basic", analysis=None):
    return ScanService(
        sessions,
        settings,
        GraphCollector(fixture),
        FixtureScanner(report),
        analysis_service=analysis,
    )


def test_graph_entities_and_dependency_api(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    service.run(accepted.scan_id)  # Duplicate delivery must not duplicate graph records.
    with TestClient(create_app(settings, sessions, service)) as client:
        base = f"/api/scans/{accepted.scan_id}"
        detail = client.get(base).json()
        assert detail["status"] == "COMPLETED"
        assert detail["analysis"]["entities"] == 11
        assert detail["analysis"]["modules"] == 1
        assert detail["analysis"]["external_modules"] == 1
        assert detail["analysis"]["external_transitive_depth_unknown"]
        assert detail["analysis"]["stages"]["persistence"]["status"] == "COMPLETED"
        assert all(
            stage["status"] == "COMPLETED" for stage in detail["analysis"]["stages"].values()
        )
        response = client.get(base + "/graph")
        assert response.status_code == 200, response.text
        graph = response.json()
        nodes = {n["id"]: n for n in graph["nodes"]}
        assert all(edge["source"] in nodes and edge["target"] in nodes for edge in graph["edges"])
        assert any(node["type"] == "UNRESOLVED" for node in graph["nodes"])
        assert any(edge["type"] == "CONSUMES_MODULE_OUTPUT" for edge in graph["edges"])
        entities = client.get(
            base + "/entities", params={"entity_type": "RESOURCE", "resource_type": "aws_instance"}
        ).json()
        assert len(entities) == 1 and entities[0]["address"] == "aws_instance.web"
        assert "module_path" in entities[0]["metadata"]
        assert entities[0]["attributes"]["subnet_id"] == "module.vpc.private_subnets[0]"
        assert client.get(base + "/entities?limit=1&offset=1").status_code == 200
        assert client.get(base + "/entities?entity_type=INVALID").status_code == 422
        assert client.get(base + "/entities?limit=101").status_code == 422
        dependencies = client.get(base + "/dependencies").json()
        assert dependencies["modules"][0]["source_type"] == "REGISTRY"
        assert dependencies["modules"][0]["version_type"] == "BOUNDED"
        assert dependencies["providers"][0]["aliases"] == ["west"]
        assert dependencies["providers"][0]["source"] == "hashicorp/aws"
        for suffix in ("entities", "graph", "dependencies"):
            assert client.get(f"/api/scans/{uuid4()}/{suffix}").status_code == 404
    with sessions() as session:
        assert session.scalar(select(func.count(ParsedEntity.id))) == 11
        assert session.scalar(select(func.count(GraphEdge.id))) == detail["analysis"]["edges"]


def test_source_trust_is_persisted_with_checkov(sessions, settings, report):
    service = service_for(sessions, settings, report, "source_trust/branch_git")
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with TestClient(create_app(settings, sessions, service)) as client:
        base = f"/api/scans/{accepted.scan_id}"
        detail = client.get(base).json()
        assert detail["summary"]["total"] == 5
        assert detail["summary"]["high"] == 2 and detail["summary"]["info"] == 1
        assert detail["analysis"]["source_trust_findings"] == 2
        findings = client.get(base + "/findings?scanner=source_trust").json()
        assert {f["rule_id"] for f in findings} == {"TFSC004", "TFSC006"}
        assert all(f["scanner"] == "source_trust" for f in findings)
        assert next(f for f in findings if f["rule_id"] == "TFSC004")["raw_data"]["ref"] == "main"


@pytest.mark.parametrize("failed_stage", ["parser", "builder", "analyzer"])
def test_graph_stage_failure_does_not_discard_checkov(sessions, settings, report, failed_stage):
    analysis = StaticAnalysisService()
    component = Mock()
    getattr(
        component,
        {"parser": "parse_directory", "builder": "build", "analyzer": "analyze"}[failed_stage],
    ).side_effect = RuntimeError("secret should not be logged")
    setattr(analysis, failed_stage, component)
    service = service_for(sessions, settings, report, analysis=analysis)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        assert scan.status == "COMPLETED" and scan.partial_result
        assert scan.total_findings == 3
        stage = {"parser": "parse", "builder": "graph", "analyzer": "source_trust"}[failed_stage]
        assert scan.analysis["stages"][stage]["status"] == "FAILED"
        assert "secret" not in str(scan.analysis)
        assert session.scalar(select(func.count(Finding.id))) == 3
        if failed_stage != "parser":
            assert session.scalar(select(func.count(ParsedEntity.id))) == 11
    assert not (settings.workspace_root / str(accepted.scan_id)).exists()


def test_checkov_failure_keeps_graph_and_trust_findings(sessions, settings, report):
    service = service_for(sessions, settings, report, "source_trust/unpinned_git")
    service.scanner.scan = Mock(side_effect=ScanTimeoutError("Scanner timed out."))
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        # Preserve MVP overall failure semantics while retaining independent static artifacts.
        assert scan.status == "FAILED" and scan.partial_result
        assert scan.analysis["stages"]["checkov"]["status"] == "FAILED"
        assert scan.analysis["stages"]["graph"]["status"] == "COMPLETED"
        assert scan.total_findings == 2
        assert session.scalar(select(func.count(ParsedEntity.id))) == 2


def test_partial_parse_with_successful_checkov(sessions, settings, report):
    analysis = StaticAnalysisService()
    parsed = HCLTerraformParser().parse_directory(FIXTURES / "dependency_graph/basic")
    parsed.diagnostics.append(
        ParseDiagnostic(
            code="FILE_PARSE_FAILED", file_path="other.tf", message="Could not parse file."
        )
    )
    parsed.files_failed = 1
    analysis.parser = Mock()
    analysis.parser.parse_directory.return_value = parsed
    service = service_for(sessions, settings, report, analysis=analysis)
    accepted = service.create("https://github.com/org/repo")
    service.run(accepted.scan_id)
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        assert scan.status == "COMPLETED" and scan.partial_result
        assert scan.analysis["stages"]["parse"]["status"] == "PARTIAL"
        assert scan.analysis["entities"] == 11 and scan.analysis["files_failed"] == 1


def test_legacy_scan_has_empty_additive_api(sessions, settings, report):
    service = service_for(sessions, settings, report)
    accepted = service.create("https://github.com/org/repo")
    with sessions() as session:
        scan = session.get(Scan, accepted.scan_id)
        scan.status = "COMPLETED"
        session.commit()
    with TestClient(create_app(settings, sessions, service)) as client:
        base = f"/api/scans/{accepted.scan_id}"
        assert client.get(base).json()["analysis"] is None
        assert client.get(base + "/graph").json()["nodes"] == []
        assert client.get(base + "/dependencies").json()["modules"] == []


def test_all_files_unparseable_stage(tmp_path):
    (tmp_path / "broken.tf").write_text('resource "bad" {')
    result = StaticAnalysisService().run(tmp_path, uuid4())
    assert result.summary.stages["parse"].status == "FAILED"
    assert result.summary.stages["graph"].status == "SKIPPED"
    assert result.summary.stages["source_trust"].status == "SKIPPED"
