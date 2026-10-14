from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

from pydantic import ValidationError

from app.core.exceptions import InvalidCheckovResultError
from app.models.enums import Severity
from app.scanners.base import ScanResult
from app.scanners.severity_overrides import SOURCE
from app.schemas.finding import NormalizedFinding
from app.schemas.scan import FindingSummary


class SeverityMapper:
    """Explicit per-rule overrides; absent severities stay UNKNOWN, never invented."""

    def __init__(self, overrides: dict[str, Severity] | None = None):
        self.overrides = overrides or {}

    def map(self, value: Any, rule_id: str) -> Severity:
        if isinstance(value, dict):
            value = value.get("level") or value.get("value")
        if isinstance(value, str):
            normalized = value.strip().upper()
            normalized = {"INFORMATIONAL": "INFO", "MODERATE": "MEDIUM"}.get(normalized, normalized)
            try:
                return Severity(normalized)
            except ValueError:
                pass
        return self.overrides.get(rule_id, Severity.UNKNOWN)

    def is_override(self, value: Any, rule_id: str) -> bool:
        """True when the scanner supplied no usable severity and the rule has an assignment."""
        if rule_id not in self.overrides:
            return False
        return SeverityMapper().map(value, rule_id) == Severity.UNKNOWN


@dataclass
class NormalizationResult:
    findings: list[NormalizedFinding] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    @property
    def partial(self) -> bool:
        return bool(self.warnings)


class FindingNormalizer(ABC):
    @abstractmethod
    def normalize(self, result: ScanResult, root: Path) -> NormalizationResult: ...


def summarize(findings: list[NormalizedFinding]) -> FindingSummary:
    counts = {severity.value.lower(): 0 for severity in Severity}
    for finding in findings:
        counts[finding.severity.value.lower()] += 1
    return FindingSummary(total=len(findings), **counts)


def normalized_path(value: Any, root: Path) -> str | None:
    if not value:
        return None
    if not isinstance(value, str) or "\\" in value or "\x00" in value:
        raise ValueError("Invalid finding path")
    # Checkov file_path is root-relative with a leading slash; some versions use absolute paths.
    root_string = str(root.resolve())
    if value.startswith(root_string + "/"):
        value = value[len(root_string) :]
    path = PurePosixPath(value)
    if ".." in path.parts:
        raise ValueError("Escaping finding path")
    return "/" + str(path).lstrip("/")


class CheckovNormalizer(FindingNormalizer):
    def __init__(self, severity_mapper: SeverityMapper | None = None):
        self.severity_mapper = severity_mapper or SeverityMapper()

    def normalize(self, result: ScanResult, root: Path) -> NormalizationResult:
        reports = result.raw_data if isinstance(result.raw_data, list) else [result.raw_data]
        output = NormalizationResult()
        for report in reports:
            if not isinstance(report, dict) or report.get("check_type", "terraform") != "terraform":
                raise InvalidCheckovResultError("Unexpected Checkov report type.")
            checks = report.get("results")
            summary = report.get("summary", {})
            if not isinstance(summary, dict):
                raise InvalidCheckovResultError("Invalid Checkov summary.")
            if checks is None and "summary" not in report and report.get("failed") == 0:
                # Nothing was evaluated: Checkov prints only flat counters.
                summary, checks = report, {}
            if checks is None and summary.get("failed") == 0:
                checks = {}  # Quiet output may omit empty results.
            if not isinstance(checks, dict):
                raise InvalidCheckovResultError("Checkov results are missing or invalid.")
            failed = checks.get("failed_checks", [])
            if not isinstance(failed, list):
                raise InvalidCheckovResultError("Checkov failed_checks must be a list.")
            if "failed_checks" not in checks and summary.get("failed") != 0:
                raise InvalidCheckovResultError("Checkov failure count is missing or inconsistent.")
            if (
                checks.get("parsing_errors")
                or report.get("parsing_errors")
                or summary.get("parsing_errors")
            ):
                output.warnings.append(
                    "Checkov reported Terraform parsing errors; coverage is incomplete."
                )
            if isinstance(summary.get("failed"), int) and summary["failed"] != len(failed):
                output.warnings.append("Checkov failure count differs from the returned results.")
            for raw in failed:
                try:
                    if not isinstance(raw, dict):
                        raise ValueError
                    rule = raw["check_id"]
                    if not isinstance(rule, str):
                        raise ValueError
                    resource = raw.get("resource")
                    resource_type = raw.get("resource_type")
                    if not resource_type and isinstance(resource, str):
                        parts = resource.split(".")
                        while len(parts) > 2 and parts[0] == "module":
                            parts = parts[2:]
                        if parts and parts[0] == "data":
                            parts = parts[1:]
                        resource_type = parts[0] if len(parts) >= 2 else None
                    lines = raw.get("file_line_range") or []
                    start, end = (lines[0], lines[1]) if len(lines) >= 2 else (None, None)
                    if start is not None and (
                        type(start) is not int or type(end) is not int or start > end
                    ):
                        raise ValueError
                    output.findings.append(
                        NormalizedFinding(
                            rule_id=rule,
                            severity=self.severity_mapper.map(raw.get("severity"), rule),
                            title=raw.get("check_name") or rule,
                            resource=resource,
                            resource_type=resource_type,
                            file_path=normalized_path(raw.get("file_path"), root),
                            line_start=start,
                            line_end=end,
                            description=raw.get("description") or raw.get("check_name"),
                            guideline=raw.get("guideline"),
                            raw_data=(
                                {**raw, "severity_source": SOURCE}
                                if self.severity_mapper.is_override(raw.get("severity"), rule)
                                else raw
                            ),
                        )
                    )
                except (KeyError, ValueError, TypeError, ValidationError):
                    output.warnings.append(
                        "A malformed Checkov finding was omitted; coverage is incomplete."
                    )
        if result.exit_code == 1 and not output.findings and not output.partial:
            raise InvalidCheckovResultError(
                "Checkov exited with violations but returned no findings."
            )
        return output
