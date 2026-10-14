from pathlib import Path

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.source_detector import detect_source
from app.core.exceptions import RegistryNotSupportedError, UnsupportedSourceError
from app.models.enums import SourceType


class TerraformRegistryCollector(SourceCollector):
    def supports(self, source: str) -> bool:
        try:
            return detect_source(source).source_type == SourceType.TERRAFORM_REGISTRY
        except UnsupportedSourceError:
            return False

    def collect(self, source: str, destination: Path) -> CollectedModule:
        raise RegistryNotSupportedError(
            "Registry download is not available in this MVP; submit the GitHub repository URL."
        )
