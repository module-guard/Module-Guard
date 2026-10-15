"""Secret detection for submitted sources.

Two detectors run together: well-known credential formats matched here (definite), and
Checkov's secrets framework (which adds entropy heuristics). Definite findings refuse the
upload; entropy findings need the user's confirmation. Secret values are never stored or
logged: a finding carries only a location and a four-character preview.
"""

import json
import logging
import re
from dataclasses import asdict, dataclass
from pathlib import Path

from app.core.config import Settings
from app.core.exceptions import DomainError, UploadRejectedError
from app.scanners.checkov import isolate_repository_config
from app.utils.subprocess import ProcessRunner, isolated_environment

logger = logging.getLogger(__name__)
MASK = "••••"
MAX_FILE_BYTES = 5 * 1024**2
# Heuristic rules: random-looking strings that are often hashes or example identifiers.
ENTROPY_RULES = {"CKV_SECRET_6", "CKV_SECRET_19"}
# Published documentation placeholders, not credentials.
DOCUMENTED_EXAMPLES = ("AKIAIOSFODNN7EXAMPLE", "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")
KNOWN_FORMATS = [
    ("AWS_ACCESS_KEY", "AWS 액세스 키", re.compile(r"\b(?:AKIA|ASIA)[0-9A-Z]{16}\b")),
    (
        "PRIVATE_KEY",
        "개인 키",
        re.compile(r"-----BEGIN (?:[A-Z0-9]+ )*PRIVATE KEY(?: BLOCK)?-----"),
    ),
    (
        "GITHUB_TOKEN",
        "GitHub 토큰",
        re.compile(r"\b(?:gh[pousr]_[A-Za-z0-9]{36,}|github_pat_[A-Za-z0-9_]{60,})\b"),
    ),
    ("SLACK_TOKEN", "Slack 토큰", re.compile(r"\bxox[abprs]-[A-Za-z0-9-]{20,}")),
    ("STRIPE_KEY", "Stripe 키", re.compile(r"\b[sr]k_live_[A-Za-z0-9]{20,}\b")),
    ("GOOGLE_API_KEY", "Google API 키", re.compile(r"\bAIza[0-9A-Za-z_-]{35}\b")),
]
QUOTED = re.compile(r"""["']([^"'\s]{8,})["']""")


class SecretScanUnavailableError(DomainError):
    """Without a completed scan nothing may be published."""

    status_code = 503


@dataclass(frozen=True)
class SecretFinding:
    file: str
    line: int
    rule: str
    title: str
    preview: str | None
    confirmed: bool

    def public(self) -> dict:
        return asdict(self)


def preview(value: str | None) -> str | None:
    """First four characters only; the rest of the value never leaves this module."""
    return f"{value[:4]}{MASK}" if value else None


def _lines(path: Path) -> list[str]:
    try:
        with path.open("rb") as handle:
            return handle.read(MAX_FILE_BYTES).decode("utf-8", errors="replace").splitlines()
    except OSError:
        return []


class SecretInspector:
    def __init__(self, settings: Settings, runner: ProcessRunner | None = None):
        self.settings = settings
        self.runner = runner or ProcessRunner()

    def inspect(self, root: Path, files: list[str]) -> list[SecretFinding]:
        found: dict[tuple[str, int], SecretFinding] = {}
        for finding in [*self._known_formats(root, files), *self._checkov(root)]:
            key = (finding.file, finding.line)
            # A definite match on the same line supersedes a heuristic one.
            if key not in found or (finding.confirmed and not found[key].confirmed):
                found[key] = finding
        return sorted(found.values(), key=lambda f: (not f.confirmed, f.file, f.line))

    def _known_formats(self, root: Path, files: list[str]) -> list[SecretFinding]:
        findings = []
        for name in files:
            for number, line in enumerate(_lines(root / name), start=1):
                for rule, title, pattern in KNOWN_FORMATS:
                    match = pattern.search(line)
                    if match and match.group(0) not in DOCUMENTED_EXAMPLES:
                        findings.append(
                            SecretFinding(name, number, rule, title, preview(match.group(0)), True)
                        )
                        break
        return findings

    def _checkov(self, root: Path) -> list[SecretFinding]:
        config = root.parent / "checkov-secrets-config.yml"
        config.write_text("{}\n")
        executable = self.settings.checkov_binary
        if "/" in executable:
            executable = str(Path(executable).resolve())
        args = [
            executable,
            "-d",
            str(root),
            "--framework",
            "secrets",
            "--enable-secret-scan-all-files",
            "-o",
            "json",
            "--quiet",
            "--compact",
            "--skip-download",
            "--config-file",
            str(config),
        ]
        try:
            with isolate_repository_config(root):
                result = self.runner.run(
                    args,
                    cwd=root.parent,
                    timeout=self.settings.secret_scan_timeout_seconds,
                    env=isolated_environment(root.parent),
                    output_limit=self.settings.max_process_output_mb * 1024**2,
                )
            # Exit 1 means findings. The output is parsed and dropped: never stored or logged.
            if result.returncode not in (0, 1):
                raise ValueError("exit")
            reports = json.loads(result.stdout)
            reports = reports if isinstance(reports, list) else [reports]
            checks = [
                check
                for report in reports
                if isinstance(report, dict) and report.get("check_type") == "secrets"
                for check in report.get("results", {}).get("failed_checks", [])
            ]
        except Exception as error:
            logger.error("action=secret_scan_failed error_type=%s", type(error).__name__)
            raise SecretScanUnavailableError(
                "비밀값 검사를 실행할 수 없어 올리기를 진행할 수 없습니다. 잠시 뒤 다시 시도해 주세요."
            ) from None
        finally:
            config.unlink(missing_ok=True)
        findings = []
        for check in checks:
            try:
                name = str(check["file_path"]).lstrip("/")
                number = int(check["file_line_range"][0])
                rule = str(check["check_id"])
                title = str(check.get("check_name") or rule)[:120]
            except (KeyError, IndexError, TypeError, ValueError):
                continue
            lines = _lines(root / name) if ".." not in Path(name).parts else []
            text = lines[number - 1] if 0 < number <= len(lines) else ""
            if any(example in text for example in DOCUMENTED_EXAMPLES):
                continue
            quoted = QUOTED.search(text)
            findings.append(
                SecretFinding(
                    name,
                    number,
                    rule,
                    title,
                    preview(quoted.group(1) if quoted else None),
                    rule not in ENTROPY_RULES,
                )
            )
        return findings


def refuse_confirmed(findings: list[SecretFinding]) -> list[SecretFinding]:
    """Raises when a definite secret is present; otherwise returns the warnings to confirm."""
    confirmed = [f for f in findings if f.confirmed]
    if confirmed:
        logger.warning(
            "action=upload_refused reason=secrets count=%s rules=%s",
            len(confirmed),
            ",".join(sorted({f.rule for f in confirmed})),
        )
        raise UploadRejectedError(
            "비밀값으로 보이는 내용이 있어 올릴 수 없습니다. 해당 값을 지우고, 이미 노출된 키는 폐기해 주세요.",
            "SecretsDetected",
            [
                {
                    "path": f.file,
                    "line": f.line,
                    "rule": f.rule,
                    "reason": f.title,
                    "preview": f.preview,
                }
                for f in confirmed[:200]
            ],
        )
    return findings
