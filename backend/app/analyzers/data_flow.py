from pathlib import Path

from app.analyzers.base import ModuleAnalyzer
from app.graphs.builder import DependencyGraphBuilder
from app.graphs.domain import DependencyGraph
from app.graphs.flow_builder import DataFlowGraphBuilder
from app.graphs.flow_domain import DataFlowResult
from app.parsers.domain import TerraformParseResult
from app.parsers.hcl import HCLTerraformParser


class DataFlowAnalyzer(ModuleAnalyzer[DataFlowResult]):
    """Produces observations, not security findings. No per-invocation state is retained."""

    def __init__(self, builder: DataFlowGraphBuilder | None = None):
        self.builder = builder or DataFlowGraphBuilder()

    def analyze(
        self,
        module_path: Path,
        *,
        parse_result: TerraformParseResult | None = None,
        graph: DependencyGraph | None = None,
    ) -> DataFlowResult:
        if graph is None:
            parsed = parse_result or HCLTerraformParser().parse_directory(module_path)
            graph = DependencyGraphBuilder().build(parsed)
        return self.builder.build(graph)
