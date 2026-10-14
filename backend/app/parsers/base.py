from abc import ABC, abstractmethod
from pathlib import Path

from app.parsers.domain import TerraformParseResult


class TerraformParser(ABC):
    @abstractmethod
    def parse_directory(self, root: Path) -> TerraformParseResult: ...
