import json
import re
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from tempfile import TemporaryDirectory

from app.core.config import Settings
from app.core.exceptions import (
    CheckovExecutionError,
    CheckovNotInstalledError,
    InvalidCheckovResultError,
)
from app.scanners.base import ScanResult, SecurityScanner
from app.utils.subprocess import ProcessRunner, isolated_environment

SKIP_PATH_PATTERN = r"(^|/)(\.git|\.terraform|node_modules|vendor|\.external_modules)(/|$)"


@contextmanager
def isolate_repository_config(target_path: Path) -> Iterator[None]:
    # Checkov merges default configs even when --config-file is provided.
    # Preserve the collected source, but hide its auto-discovered settings during execution.
    with TemporaryDirectory(prefix="ignored-config-", dir=target_path.parent) as directory:
        moved: list[tuple[Path, Path]] = []
        try:
            for name in (".checkov.yml", ".checkov.yaml"):
                source = target_path / name
                if source.exists() or source.is_symlink():
                    isolated = Path(directory) / name
                    source.rename(isolated)
                    moved.append((isolated, source))
            yield
        finally:
            for isolated, source in reversed(moved):
                isolated.rename(source)


class CheckovScanner(SecurityScanner):
    name = "checkov"

    def __init__(self, settings: Settings, runner: ProcessRunner | None = None):
        self.settings = settings
        self.runner = runner or ProcessRunner()

    def version(self, target_path: Path) -> str | None:
        """Query the actual scanner executable, with bounded, isolated execution."""
        executable = self.settings.checkov_binary
        if "/" in executable:
            executable = str(Path(executable).resolve())
        try:
            result = self.runner.run(
                [executable, "--version"],
                cwd=target_path.parent,
                timeout=min(self.settings.checkov_timeout_seconds, 10),
                env=isolated_environment(target_path.parent),
                output_limit=4096,
            )
        except Exception:
            # A metadata lookup must never fail the scan or expose tool output.
            return None
        if result.returncode != 0:
            return None
        text = result.stdout.strip()
        return text if re.fullmatch(r"\d+(?:\.\d+){1,3}(?:[a-zA-Z0-9.+-]{0,24})?", text) else None

    def scan(self, target_path: Path) -> ScanResult:
        # Config isolation, controlled cwd and environment prevent custom code/policy injection.
        config = target_path.parent / "checkov-config.yml"
        config.write_text("{}\n")
        executable = self.settings.checkov_binary
        if "/" in executable:
            executable = str(Path(executable).resolve())
        args = [
            executable,
            "-d",
            str(target_path),
            "--framework",
            "terraform",
            "-o",
            "json",
            "--quiet",
            "--compact",
            "--skip-download",
            "--download-external-modules",
            "false",
            "--evaluate-variables",
            "false",
            "--config-file",
            str(config),
            "--skip-path",
            SKIP_PATH_PATTERN,
        ]
        try:
            with isolate_repository_config(target_path):
                result = self.runner.run(
                    args,
                    cwd=target_path.parent,
                    timeout=self.settings.checkov_timeout_seconds,
                    env=isolated_environment(target_path.parent),
                    output_limit=self.settings.max_process_output_mb * 1024**2,
                )
        except FileNotFoundError:
            raise CheckovNotInstalledError("Checkov binary is not installed.") from None
        # Exit 1 means policy violations, not execution failure.
        if result.returncode not in (0, 1):
            raise CheckovExecutionError(f"Checkov failed with exit code {result.returncode}.")
        try:
            raw = json.loads(result.stdout)
        except (ValueError, TypeError):
            raise InvalidCheckovResultError("Checkov did not return valid JSON.") from None
        if not isinstance(raw, (dict, list)):
            raise InvalidCheckovResultError("Checkov returned an invalid report structure.")
        return ScanResult(raw, result.returncode)
