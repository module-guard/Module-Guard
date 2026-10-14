import pytest

from app.core.exceptions import InvalidCheckovResultError
from app.models.enums import Severity
from app.scanners.base import ScanResult
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper, summarize


@pytest.mark.parametrize(
    "value,expected",
    [
        (None, "UNKNOWN"),
        ("high", "HIGH"),
        ("CRITICAL", "CRITICAL"),
        ("informational", "INFO"),
        ({"level": "LOW"}, "LOW"),
        ("invalid", "UNKNOWN"),
        (42, "UNKNOWN"),
    ],
)
def test_severity(value, expected):
    assert SeverityMapper().map(value, "RULE") == expected


def test_severity_override():
    mapper = SeverityMapper({"RULE": Severity.MEDIUM})
    assert mapper.map(None, "RULE") == Severity.MEDIUM
    assert mapper.map("HIGH", "RULE") == Severity.HIGH


@pytest.mark.parametrize("as_list", [False, True])
def test_normalize_and_summary(report, tmp_path, as_list):
    normalized = CheckovNormalizer().normalize(
        ScanResult([report] if as_list else report, 1), tmp_path
    )
    assert not normalized.partial
    first = normalized.findings[0]
    assert first.rule_id == "CKV_AWS_24"
    assert first.line_start == 1 and first.line_end == 9
    assert first.resource_type == "aws_security_group"
    assert normalized.findings[2].resource_type == "aws_s3_bucket"
    assert first.raw_data == report["results"]["failed_checks"][0]
    assert summarize(normalized.findings).model_dump() == dict(
        total=3, critical=1, high=1, medium=0, low=0, info=0, unknown=1
    )


@pytest.mark.parametrize(
    "raw", [[], {"summary": {"failed": 0}}, {"results": {"failed_checks": []}}]
)
def test_valid_empty(raw, tmp_path):
    assert CheckovNormalizer().normalize(ScanResult(raw, 0), tmp_path).findings == []


@pytest.mark.parametrize(
    "raw",
    [
        {},
        {"results": []},
        {"results": {"failed_checks": {}}},
        {"check_type": "secrets", "results": {}},
        [1],
        {"summary": {"failed": 2}},
    ],
)
def test_bad_report(raw, tmp_path):
    with pytest.raises(InvalidCheckovResultError):
        CheckovNormalizer().normalize(ScanResult(raw, 0), tmp_path)


def test_exit_one_without_findings(tmp_path):
    with pytest.raises(InvalidCheckovResultError):
        CheckovNormalizer().normalize(ScanResult([], 1), tmp_path)


def test_partial_and_escaping_path(report, tmp_path):
    report["results"]["parsing_errors"] = ["broken.tf"]
    report["results"]["failed_checks"][0]["file_path"] = "/../../secret.tf"
    result = CheckovNormalizer().normalize(ScanResult(report, 1), tmp_path)
    assert result.partial
    assert len(result.findings) == 2
    assert len(result.warnings) == 2


def test_absolute_path(report, tmp_path):
    report["results"]["failed_checks"][0]["file_path"] = str(tmp_path / "main.tf")
    result = CheckovNormalizer().normalize(ScanResult(report, 1), tmp_path)
    assert result.findings[0].file_path == "/main.tf"


def test_flat_counters_for_a_module_with_nothing_to_check(tmp_path):
    # Checkov 3 prints this instead of a report when no resource matched any check.
    report = {
        "passed": 0,
        "failed": 0,
        "skipped": 0,
        "parsing_errors": 0,
        "resource_count": 0,
        "checkov_version": "3.3.26",
    }
    result = CheckovNormalizer().normalize(ScanResult(report, 0), tmp_path)
    assert result.findings == [] and not result.partial
    with pytest.raises(InvalidCheckovResultError):
        CheckovNormalizer().normalize(ScanResult({**report, "failed": 2}, 1), tmp_path)
