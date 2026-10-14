from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class ScanResult:
    raw_data: dict[str, Any] | list[Any]
    exit_code: int


class SecurityScanner(ABC):
    name: str

    @abstractmethod
    def scan(self, target_path: Path) -> ScanResult: ...
