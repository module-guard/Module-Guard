"""Which files of a submitted module may be stored. Shared by zip uploads and GitHub imports."""

import os
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath

from app.core.exceptions import UnsafeRepositoryError, UploadRejectedError
from app.storage.snapshots import is_excluded as is_secret_bearing

ALLOWED_SUFFIXES = (
    ".tf",
    ".tf.json",
    ".tftpl",
    ".md",
    ".json",
    ".yaml",
    ".yml",
    ".tfvars.example",
    # Files modules load with templatefile()/file(): stored as text, never executed here.
    ".tpl",
    ".sh",
    ".py",
)
# Harmless templates that share a sensitive prefix: left out, but not a reason to refuse.
TEMPLATE_SUFFIXES = (".example", ".sample", ".template", ".dist")
SKIPPED_DIRECTORIES = {".git"}
REPORT_LIMIT = 200


class Decision(StrEnum):
    INCLUDE = "include"
    EXCLUDE = "exclude"
    SENSITIVE = "sensitive"


def classify(path: PurePosixPath) -> tuple[Decision, str | None]:
    """Decision for one relative file path, with the reason shown to the user."""
    name = path.name.lower()
    if ".terraform" in path.parts[:-1]:
        return Decision.SENSITIVE, "Terraform 작업 폴더(.terraform/)는 올릴 수 없습니다."
    if name.endswith(".tfvars.example"):
        return Decision.INCLUDE, None
    if is_secret_bearing(name):
        if name.endswith(TEMPLATE_SUFFIXES):
            return Decision.EXCLUDE, "예시 설정 파일은 저장하지 않습니다."
        if ".tfstate" in name:
            return Decision.SENSITIVE, "Terraform state 파일에는 실제 리소스 정보가 들어 있습니다."
        if name.endswith((".tfvars", ".tfvars.json")):
            return Decision.SENSITIVE, "tfvars 파일에는 실제 값이 들어 있을 수 있습니다."
        return Decision.SENSITIVE, "키·인증서·환경 변수 파일은 올릴 수 없습니다."
    if name.endswith(ALLOWED_SUFFIXES) or name.split(".")[0] == "license":
        return Decision.INCLUDE, None
    return Decision.EXCLUDE, "허용된 파일 형식이 아닙니다."


@dataclass
class SourceReport:
    included: list[str] = field(default_factory=list)
    excluded: list[dict] = field(default_factory=list)
    size_bytes: int = 0

    def exclude(self, path: str, reason: str) -> None:
        self.excluded.append({"path": path, "reason": reason})


def reject_sensitive(found: list[dict]) -> None:
    if found:
        raise UploadRejectedError(
            "민감한 파일이 포함되어 있어 올릴 수 없습니다. 해당 파일을 빼고 다시 올려 주세요.",
            "SensitiveFiles",
            found[:REPORT_LIMIT],
        )


def require_terraform(report: SourceReport) -> None:
    if not any(path.endswith((".tf", ".tf.json")) for path in report.included):
        raise UploadRejectedError(
            "Terraform 파일(.tf)이 없습니다.", "NoTerraformFiles", report.excluded[:REPORT_LIMIT]
        )


def filter_tree(root: Path) -> SourceReport:
    """Apply the policy to a collected directory in place (GitHub imports).

    Excluded files are deleted so later stages only ever see what will be stored.
    """
    report, sensitive = SourceReport(), []
    for directory, dirs, names in os.walk(root, followlinks=False):
        for name in [*dirs, *names]:
            if (Path(directory) / name).is_symlink():
                raise UnsafeRepositoryError(
                    "Repositories containing symbolic links are not supported."
                )
        dirs[:] = [d for d in dirs if d not in SKIPPED_DIRECTORIES]
        for name in names:
            entry = Path(directory) / name
            if not entry.is_file():
                raise UnsafeRepositoryError("Repository contains a non-regular file.")
            relative = PurePosixPath(entry.relative_to(root).as_posix())
            decision, reason = classify(relative)
            if decision == Decision.SENSITIVE:
                sensitive.append({"path": str(relative), "reason": reason})
            elif decision == Decision.EXCLUDE:
                report.exclude(str(relative), reason)
                entry.unlink()
            else:
                report.included.append(str(relative))
                report.size_bytes += entry.stat().st_size
    reject_sensitive(sensitive)
    report.included.sort()
    require_terraform(report)
    return report
