from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path

from app.models.enums import SourceType


@dataclass(frozen=True)
class SourceIdentity:
    source_type: SourceType
    source_url: str
    owner: str
    repository_name: str
    provider: str | None = None


@dataclass(frozen=True)
class CollectedModule:
    source_type: SourceType
    source_url: str
    local_path: Path
    repository_name: str
    owner: str
    branch: str
    commit_hash: str


class SourceCollector(ABC):
    @abstractmethod
    def supports(self, source: str) -> bool: ...

    @abstractmethod
    def collect(self, source: str, destination: Path, ref: str | None = None) -> CollectedModule:
        """Fetch the source. `ref` pins a release tag or a full commit where supported."""
