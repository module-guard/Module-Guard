from collections.abc import Callable
from pathlib import Path

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.github import GitHubCollector
from app.collectors.terraform_registry import TerraformRegistryCollector
from app.core.config import Settings
from app.core.exceptions import UnsupportedSourceError


class SourceRouter(SourceCollector):
    """Pick the collector that recognises a source, so callers need not know its kind."""

    def __init__(
        self, collectors: list[SourceCollector], guard: Callable[[str], None] | None = None
    ):
        self.collectors = collectors
        self.guard = guard

    def supports(self, source: str) -> bool:
        return any(collector.supports(source) for collector in self.collectors)

    def collect(self, source: str, destination: Path, ref: str | None = None) -> CollectedModule:
        if self.guard is not None:
            self.guard(source)
        for collector in self.collectors:
            if collector.supports(source):
                return collector.collect(source, destination, ref)
        raise UnsupportedSourceError(
            "Use https://github.com/owner/repository or namespace/name/provider."
        )


def default_router(settings: Settings, guard: Callable[[str], None] | None = None) -> SourceRouter:
    """GitHub first, then Registry; the Registry collector reuses the one GitHub clone path.

    `guard` is the allow/block check; every collector in the router shares it."""
    github = GitHubCollector(settings, guard=guard)
    return SourceRouter(
        [github, TerraformRegistryCollector(settings, github, guard=guard)], guard=guard
    )
