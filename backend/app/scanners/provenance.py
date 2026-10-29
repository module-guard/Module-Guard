"""Non-secret, per-scan tool/rule provenance stored in the existing scans.analysis JSON."""

import hashlib
import os
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path

from app.core.config import Settings
from app.scanners.checkov import SKIP_PATH_PATTERN, CheckovScanner

# Hash the deployed rule code, not the checkout's current git commit. This changes when
# rule logic changes even when the package version stays at 0.1.0.
_RULE_FILES = (
    "scanners/checkov.py",
    "scanners/normalizer.py",
    "scanners/severity_overrides.py",
    "scanners/remediation.py",
    "scanners/scope.py",
    "analyzers/source_trust.py",
    "correlation/engine.py",
    "correlation/scoring.py",
    "correlation/rules.py",
)
_OPTIONS = (
    "checkov_timeout_seconds",
    "max_process_output_mb",
    "max_repository_size_mb",
    "max_analysis_entities",
    "max_analysis_edges",
    "max_local_module_depth",
    "max_terraform_file_bytes",
    "max_flow_edges",
    "max_flow_paths",
    "max_flow_depth",
    "max_flow_search_steps",
)
_RULE_ROOT = Path(__file__).resolve().parents[1]


def _digest_files(paths: tuple[str, ...]) -> str | None:
    """Return a reproducible digest or unknown if any deployed rule file is absent."""
    digest = hashlib.sha256()
    try:
        for name in paths:
            content = (_RULE_ROOT / name).read_bytes()
            digest.update(name.encode("utf-8") + b"\0")
            digest.update(len(content).to_bytes(8, "big") + content)
    except OSError:
        return None
    return digest.hexdigest()


def rule_versions() -> dict[str, str | None]:
    return {
        "severity_rules_version": _digest_files(("scanners/severity_overrides.py",)),
        "remediation_rules_version": _digest_files(("scanners/remediation.py",)),
        "rules_version": _digest_files(_RULE_FILES),
    }


def capture_provenance(settings: Settings, scanner, root: Path) -> dict:
    """Capture at scan time; never infer a missing historical version later."""
    try:
        release = version("terraform-module-intelligence")
    except PackageNotFoundError:
        release = None
    commit = os.environ.get("MODULEGUARD_BUILD_SHA", "")
    # An explicitly injected build SHA is safe to expose; never expose arbitrary env values.
    if not re.fullmatch(r"[0-9a-fA-F]{40}|[0-9a-fA-F]{64}", commit):
        commit = None
    checkov_version = scanner.version(root) if isinstance(scanner, CheckovScanner) else None
    return {
        "moduleguard_version": release,
        "build_commit": commit,
        "scanner_name": scanner.name,
        "checkov_version": checkov_version,
        **rule_versions(),
        "analysis_options": {name: getattr(settings, name) for name in _OPTIONS},
        "checkov_options": {
            "framework": "terraform",
            "skip_download": True,
            "skip_path": SKIP_PATH_PATTERN,
            "download_external_modules": False,
            "evaluate_variables": False,
            "repository_config_isolated": True,
        },
    }
