import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID

from app.analyzers.base import ModuleAnalyzer
from app.analyzers.data_flow import DataFlowAnalyzer
from app.analyzers.source_trust import SourceTrustAnalyzer
from app.graphs.builder import DependencyGraphBuilder
from app.graphs.domain import DependencyGraph
from app.parsers.base import TerraformParser
from app.parsers.domain import EntityType
from app.parsers.hcl import HCLTerraformParser
from app.schemas.analysis import AnalysisStage, AnalysisSummary, StageStatus
from app.schemas.finding import NormalizedFinding

logger = logging.getLogger(__name__)


@dataclass
class StaticAnalysisResult:
    graph: DependencyGraph
    findings: list[NormalizedFinding]
    summary: AnalysisSummary


class StaticAnalysisService:
    """Independent stage outcomes; failures do not discard earlier valid artifacts."""

    def __init__(
        self,
        parser: TerraformParser | None = None,
        builder: DependencyGraphBuilder | None = None,
        analyzer: ModuleAnalyzer[list[NormalizedFinding]] | None = None,
        flow_analyzer: DataFlowAnalyzer | None = None,
    ):
        self.parser = parser or HCLTerraformParser()
        self.builder = builder or DependencyGraphBuilder()
        self.analyzer = analyzer or SourceTrustAnalyzer()
        self.flow_analyzer = flow_analyzer or DataFlowAnalyzer()

    def run(
        self, root: Path, scan_id: UUID, publish: Callable[[AnalysisSummary], None] | None = None
    ) -> StaticAnalysisResult:
        summary = AnalysisSummary(
            stages={
                name: AnalysisStage() for name in ("parse", "graph", "source_trust", "data_flow")
            }
        )
        graph = DependencyGraph()
        findings = []

        def stage(name: str, status: StageStatus, error: Exception | None = None) -> None:
            summary.stages[name] = AnalysisStage(
                status=status,
                error_code=type(error).__name__ if error else None,
                message=f"{name} analysis could not complete." if error else None,
            )
            if publish:
                publish(summary)
            if error:
                logger.error(
                    "scan_id=%s action=%s_failed error_type=%s", scan_id, name, type(error).__name__
                )

        stage("parse", StageStatus.RUNNING)
        try:
            parsed = self.parser.parse_directory(root)
        except Exception as error:
            stage("parse", StageStatus.FAILED, error)
            stage("graph", StageStatus.SKIPPED)
            stage("source_trust", StageStatus.SKIPPED)
            stage("data_flow", StageStatus.SKIPPED)
            return StaticAnalysisResult(graph, findings, summary)
        summary.files_parsed = parsed.files_parsed
        summary.files_failed = parsed.files_failed
        summary.diagnostics = [d.model_dump(mode="json") for d in parsed.diagnostics]
        summary.entities = len(parsed.entities)
        if parsed.files_failed and not parsed.files_parsed:
            stage("parse", StageStatus.FAILED)
            stage("graph", StageStatus.SKIPPED)
            stage("source_trust", StageStatus.SKIPPED)
            stage("data_flow", StageStatus.SKIPPED)
            return StaticAnalysisResult(DependencyGraph(nodes=parsed.entities), findings, summary)
        stage("parse", StageStatus.PARTIAL if parsed.diagnostics else StageStatus.COMPLETED)
        logger.info(
            "scan_id=%s action=terraform_parse files=%s entities=%s",
            scan_id,
            parsed.files_parsed,
            len(parsed.entities),
        )
        graph = DependencyGraph(nodes=parsed.entities)
        stage("graph", StageStatus.RUNNING)
        try:
            graph = self.builder.build(parsed)
        except Exception as error:
            stage("graph", StageStatus.FAILED, error)
            stage("source_trust", StageStatus.SKIPPED)
            stage("data_flow", StageStatus.SKIPPED)
            return StaticAnalysisResult(graph, findings, summary)
        summary.edges = len(graph.edges)
        summary.dependency_edges = len(graph.edges)
        summary.unresolved_references = sum(e.target_entity_id is None for e in graph.edges)
        modules = [n for n in graph.nodes if n.entity_type == EntityType.MODULE]
        summary.modules = len(modules)
        summary.external_modules = sum(
            n.metadata.get("source_type") in {"REGISTRY", "GITHUB", "GIT", "HTTP"} for n in modules
        )
        summary.providers = sum(n.entity_type == EntityType.REQUIRED_PROVIDER for n in graph.nodes)
        summary.max_observed_module_depth = max(
            (len(n.metadata.get("module_path", [])) + 1 for n in modules), default=0
        )
        summary.external_transitive_depth_unknown = summary.external_modules > 0
        summary.diagnostics.extend(
            {"code": "EXPRESSION_ANALYSIS_FAILED", "message": message}
            for message in graph.diagnostics
        )
        partial = bool(summary.diagnostics)
        stage("graph", StageStatus.PARTIAL if partial else StageStatus.COMPLETED)
        logger.info(
            "scan_id=%s action=graph_build nodes=%s edges=%s",
            scan_id,
            len(graph.nodes),
            len(graph.edges),
        )
        stage("source_trust", StageStatus.RUNNING)
        try:
            findings = self.analyzer.analyze(root, parse_result=parsed, graph=graph)
        except Exception as error:
            stage("source_trust", StageStatus.FAILED, error)
        else:
            stage("source_trust", StageStatus.PARTIAL if partial else StageStatus.COMPLETED)
        summary.source_trust_findings = len(findings)
        logger.info("scan_id=%s action=source_trust findings=%s", scan_id, len(findings))
        stage("data_flow", StageStatus.RUNNING)
        logger.info("scan_id=%s action=data_flow_start", scan_id)
        try:
            flow = self.flow_analyzer.analyze(root, parse_result=parsed, graph=graph)
            graph.edges.extend(flow.edges)
            summary.data_flow = flow.summary
            summary.edges = len(graph.edges)
            summary.diagnostics.extend(flow.diagnostics)
            stage(
                "data_flow",
                StageStatus.PARTIAL if partial or flow.diagnostics else StageStatus.COMPLETED,
            )
            logger.info(
                "scan_id=%s action=data_flow_complete flows=%s cross_module=%s unresolved=%s",
                scan_id,
                flow.summary.flows,
                flow.summary.cross_module_flows,
                flow.summary.unresolved_flows,
            )
        except Exception as error:
            stage("data_flow", StageStatus.FAILED, error)
        return StaticAnalysisResult(graph, findings, summary)
