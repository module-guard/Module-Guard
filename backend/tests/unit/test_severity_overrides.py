import pytest

from app.models.enums import Severity
from app.scanners.base import ScanResult
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper
from app.scanners.severity_overrides import SEVERITY_OVERRIDES, SOURCE

# The approved table, restated so an accidental edit of the source fails here.
APPROVED = {
    "HIGH": "CKV_AWS_109 CKV_AWS_38 CKV_AWS_39 CKV_AWS_53 CKV_AWS_54 CKV_AWS_55 CKV_AWS_56"
    " CKV2_AWS_6 CKV_AWS_79",
    "MEDIUM": "CKV_AWS_356 CKV_AWS_355 CKV_AWS_111 CKV2_AWS_12 CKV_AWS_58 CKV_AWS_88 CKV_AWS_19"
    " CKV_AWS_145 CKV_AWS_27 CKV_AWS_8 CKV_AWS_37 CKV2_AWS_11 CKV_AWS_341 CKV_AWS_9 CKV_AWS_12"
    " CKV_AWS_14 CKV2_AWS_22 CKV_AWS_40 CKV2_AWS_65 CKV_AWS_148",
    "LOW": "CKV_AWS_273 CKV_TF_1 CKV_AWS_21 CKV_AWS_144 CKV_AWS_300 CKV2_AWS_62 CKV_AWS_338"
    " CKV_AWS_394 CKV_AWS_23 CKV2_AWS_5 CKV2_AWS_19 CKV_AWS_126 CKV_AWS_135 CKV2_AWS_41"
    " CKV2_AWS_14 CKV2_AWS_21",
}


def test_table_matches_the_approved_assignments():
    expected = {
        rule: Severity(level) for level, rules in APPROVED.items() for rule in rules.split()
    }
    assert SEVERITY_OVERRIDES == expected
    assert len(expected) == 45
    assert Severity.CRITICAL not in SEVERITY_OVERRIDES.values()


def report(checks):
    return ScanResult(
        {
            "check_type": "terraform",
            "results": {"failed_checks": checks},
            "summary": {"failed": len(checks)},
        },
        1,
    )


def check(rule, **extra):
    return {
        "check_id": rule,
        "check_name": rule,
        "resource": "aws_x.y",
        "file_path": "/main.tf",
        **extra,
    }


def test_assignment_applies_only_without_a_scanner_severity(tmp_path):
    normalizer = CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES))
    findings = normalizer.normalize(
        report(
            [
                check("CKV_AWS_109"),
                check("CKV_AWS_356"),
                check("CKV_TF_1"),
                check("CKV_AWS_109", severity="LOW"),
                check("CKV_AWS_999"),
                check("CKV_AWS_79", severity="not-a-level"),
            ]
        ),
        tmp_path,
    ).findings
    assert [(f.rule_id, f.severity.value, f.raw_data.get("severity_source")) for f in findings] == [
        ("CKV_AWS_109", "HIGH", SOURCE),
        ("CKV_AWS_356", "MEDIUM", SOURCE),
        ("CKV_TF_1", "LOW", SOURCE),
        # Checkov's own severity wins and is not relabelled.
        ("CKV_AWS_109", "LOW", None),
        # Unlisted rules are never guessed.
        ("CKV_AWS_999", "UNKNOWN", None),
        ("CKV_AWS_79", "HIGH", SOURCE),
    ]


@pytest.mark.parametrize("rule", sorted(SEVERITY_OVERRIDES))
def test_default_mapper_leaves_everything_unknown(rule):
    assert SeverityMapper().map(None, rule) == Severity.UNKNOWN
