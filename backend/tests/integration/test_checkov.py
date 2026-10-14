import os
import shutil
import sys
from pathlib import Path

import pytest
from conftest import FIXTURES

from app.scanners.checkov import CheckovScanner
from app.scanners.normalizer import CheckovNormalizer
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
