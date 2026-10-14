from abc import ABC, abstractmethod
from pathlib import Path
from typing import TYPE_CHECKING, Generic, TypeVar

if TYPE_CHECKING:
    from app.graphs.domain import DependencyGraph
    from app.parsers.domain import TerraformParseResult


AnalysisResult = TypeVar("AnalysisResult")


class ModuleAnalyzer(ABC, Generic[AnalysisResult]):
    """Reuse parsed semantic artifacts; positional path entry point remains supported."""

    @abstractmethod
    def analyze(
        self,
        module_path: Path,
        *,
        parse_result: "TerraformParseResult | None" = None,
        graph: "DependencyGraph | None" = None,
    ) -> AnalysisResult: ...
