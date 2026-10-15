import os
import shutil
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES

from app.models.enums import Severity
from app.scanners.checkov import CheckovScanner
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper
from app.scanners.remediation import REMEDIATIONS
from app.scanners.severity_overrides import SEVERITY_OVERRIDES
from app.utils.filesystem import terraform_files
from app.utils.terraform import validate_local_module_paths

BINARY = os.getenv("CHECKOV_BINARY") or str(
    Path(__file__).parents[2] / ".venv-checkov" / "bin" / "checkov"
)
if not Path(BINARY).is_file():
    BINARY = shutil.which("checkov") or str(Path(sys.executable).parent / "checkov")
BINARY = str(Path(BINARY).resolve())


@pytest.mark.checkov
@pytest.mark.skipif(not Path(BINARY).is_file(), reason="Checkov is not installed")
@pytest.mark.parametrize(
    "fixture,rule",
    [
        ("insecure_s3", "CKV_AWS_53"),
        ("open_security_group", "CKV_AWS_24"),
        ("public_rds", "CKV_AWS_17"),
    ],
)
def test_real_checkov(settings, tmp_path, fixture, rule):
    target = tmp_path / "module"
    shutil.copytree(FIXTURES / fixture, target)
    if fixture == "open_security_group":
        # A repository must not override policy selection or import custom Python checks.
        custom_checks = target / "custom_checks"
        custom_checks.mkdir()
        (custom_checks / "untrusted.py").write_text(
            f"from pathlib import Path\nPath({str(tmp_path / 'executed')!r}).touch()\n"
        )
        (target / ".checkov.yml").write_text(
            f"skip-check: [CKV_AWS_24]\nexternal-checks-dir: [{custom_checks}]\n"
        )
    settings.checkov_binary = BINARY
    validate_local_module_paths(terraform_files(target), target)
    result = CheckovScanner(settings).scan(target)
    normalized = CheckovNormalizer().normalize(result, target)
    assert normalized.findings
    assert rule in {finding.rule_id for finding in normalized.findings}
    assert all(f.check_result == "FAILED" for f in normalized.findings)

    assert not (tmp_path / "executed").exists()


ADMIN_POLICY = """
resource "aws_iam_policy" "admin" {
  name   = "admin"
  policy = jsonencode({
    Version   = "2012-10-17"
    Statement = [{ Effect = "Allow", Action = "*", Resource = "*" }]
  })
}
"""


@pytest.mark.checkov
@pytest.mark.skipif(not Path(BINARY).is_file(), reason="Checkov is not installed")
def test_real_checkov_admin_policy_is_high_with_a_guideline(settings, tmp_path):
    # With --skip-download Checkov sends neither severity nor guideline for these rules;
    # ModuleGuard supplies both, so TFCR001 can see CKV_AWS_62/63 as HIGH.
    target = tmp_path / "module"
    target.mkdir()
    (target / "main.tf").write_text(ADMIN_POLICY)
    settings.checkov_binary = BINARY
    result = CheckovScanner(settings).scan(target)
    normalized = CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES)).normalize(result, target)
    by_rule = {finding.rule_id: finding for finding in normalized.findings}
    assert {"CKV_AWS_62", "CKV_AWS_63"} <= set(by_rule)
    for rule in ("CKV_AWS_62", "CKV_AWS_63"):
        finding = by_rule[rule]
        assert not finding.raw_data.get("severity") and not result_guideline(result, rule)
        assert finding.severity == Severity.HIGH
        assert finding.raw_data["severity_source"] == "moduleguard"
        assert finding.guideline == REMEDIATIONS[rule]
        assert finding.raw_data["guideline_source"] == "moduleguard"


def result_guideline(result, rule):
    reports = result.raw_data if isinstance(result.raw_data, list) else [result.raw_data]
    return next(
        check.get("guideline")
        for report in reports
        for check in report["results"]["failed_checks"]
        if check["check_id"] == rule
    )
