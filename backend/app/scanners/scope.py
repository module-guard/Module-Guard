"""Where a finding comes from: the module itself, or its examples and tests.

A finding counts as EXAMPLE when the file it is in, or the file that instantiated the module
it is in (Checkov's caller_file_path), lies under examples/, example/, test/ or tests/.
Badges and default numbers describe the module body only.
"""

from collections import Counter
from typing import Any

MODULE = "MODULE"
EXAMPLE = "EXAMPLE"
EXAMPLE_DIRECTORIES = {"examples", "example", "test", "tests"}
LEVELS = ("critical", "high", "medium", "low", "info", "unknown")


def _top(path: Any) -> str | None:
    if not isinstance(path, str) or not path:
        return None
    return path.replace("\\", "/").lstrip("/").split("/", 1)[0].lower()


def finding_scope(file_path: str | None, raw_data: dict[str, Any] | None) -> str:
    caller = (raw_data or {}).get("caller_file_path")
    if _top(file_path) in EXAMPLE_DIRECTORIES or _top(caller) in EXAMPLE_DIRECTORIES:
        return EXAMPLE
    return MODULE


def scope_summary(findings) -> dict:
    """Example totals and per-scanner counts for each scope (findings carry .scope)."""
    example = Counter()
    by_scanner = {MODULE: Counter(), EXAMPLE: Counter()}
    for finding in findings:
        by_scanner[finding.scope][finding.scanner] += 1
        if finding.scope == EXAMPLE:
            severity = getattr(finding.severity, "value", finding.severity)
            example[str(severity).lower()] += 1
    return {
        "example": {"total": sum(example.values()), **{level: example[level] for level in LEVELS}},
        "module_by_scanner": dict(by_scanner[MODULE]),
        "example_by_scanner": dict(by_scanner[EXAMPLE]),
    }
