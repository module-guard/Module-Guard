from pathlib import Path

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.github import GitHubCollector
from app.collectors.terraform_registry import TerraformRegistryCollector
from app.core.config import Settings
from app.core.exceptions import UnsupportedSourceError


class SourceRouter(SourceCollector):
    """Pick the collector that recognises a source, so callers need not know its kind."""

    def __init__(self, collectors: list[SourceCollector]):
        self.collectors = collectors

    def supports(self, source: str) -> bool:
        return any(collector.supports(source) for collector in self.collectors)

    def collect(self, source: str, destination: Path, ref: str | None = None) -> CollectedModule:
        for collector in self.collectors:
            if collector.supports(source):
                return collector.collect(source, destination, ref)
        raise UnsupportedSourceError(
            "Use https://github.com/owner/repository or namespace/name/provider."
        )


def default_router(settings: Settings) -> SourceRouter:
    """GitHub first, then Registry; the Registry collector reuses the one GitHub clone path."""
    github = GitHubCollector(settings)
    return SourceRouter([github, TerraformRegistryCollector(settings, github)])
