import pytest

from app.correlation.scoring import PRIVILEGED_IAM_RULES
from app.models.enums import Severity
from app.scanners.base import ScanResult
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper
from app.scanners.severity_overrides import SEVERITY_OVERRIDES, SOURCE

# The approved table, restated so an accidental edit of the source fails here.
APPROVED = {
    "HIGH": "CKV_AWS_109 CKV_AWS_38 CKV_AWS_39 CKV_AWS_53 CKV_AWS_54 CKV_AWS_55 CKV_AWS_56"
    " CKV2_AWS_6 CKV_AWS_79 CKV_AWS_62 CKV_AWS_63 CKV_AWS_1 CKV_AWS_49 CKV_AWS_286 CKV_AWS_110"
    " CKV_AWS_287 CKV_AWS_107 CKV_AWS_289 CKV_AWS_283 CKV_AWS_70 CKV_AWS_20 CKV_AWS_57"
    " CKV_AWS_17 CKV_AWS_24 CKV_AWS_25 CKV_AWS_260 CKV_AWS_277 CKV_AWS_258 CKV_AWS_45",
    "MEDIUM": "CKV_AWS_356 CKV_AWS_355 CKV_AWS_111 CKV_AWS_290 CKV_AWS_288 CKV_AWS_108"
    " CKV2_AWS_12 CKV_AWS_382 CKV_AWS_58 CKV_AWS_88 CKV_AWS_19 CKV_AWS_145 CKV_AWS_18"
    " CKV_AWS_27 CKV_AWS_26 CKV_AWS_8 CKV_AWS_16 CKV_AWS_129 CKV_AWS_42 CKV_AWS_184 CKV_AWS_43"
    " CKV_AWS_185 CKV_AWS_119 CKV_AWS_158 CKV_AWS_149 CKV_AWS_209 CKV_AWS_48 CKV_AWS_136"
    " CKV_AWS_51 CKV_AWS_163 CKV_AWS_173 CKV_AWS_272 CKV_AWS_304 CKV2_AWS_57 CKV_AWS_37"
    " CKV2_AWS_11 CKV_AWS_341 CKV_AWS_9 CKV_AWS_10 CKV_AWS_11 CKV_AWS_12 CKV_AWS_14 CKV_AWS_15"
    " CKV2_AWS_22 CKV_AWS_40 CKV2_AWS_65 CKV_AWS_148",
    "LOW": "CKV_AWS_273 CKV_TF_1 CKV_TF_2 CKV_AWS_21 CKV_AWS_144 CKV_AWS_300 CKV2_AWS_61"
    " CKV2_AWS_62 CKV_AWS_93 CKV_AWS_338 CKV_AWS_394 CKV_AWS_23 CKV2_AWS_5 CKV2_AWS_19"
    " CKV_AWS_126 CKV_AWS_135 CKV2_AWS_41 CKV2_AWS_14 CKV2_AWS_21 CKV_AWS_353 CKV_AWS_118"
    " CKV_AWS_157 CKV_AWS_293 CKV_AWS_133 CKV_AWS_226 CKV_AWS_211 CKV2_AWS_60 CKV_AWS_28"
    " CKV_AWS_207 CKV_AWS_50 CKV_AWS_115 CKV_AWS_116 CKV_AWS_117 CKV_AWS_363",
}


def test_table_matches_the_approved_assignments():
    listed = [rule for rules in APPROVED.values() for rule in rules.split()]
    assert len(listed) == len(set(listed)), "a rule is listed under two levels"
    expected = {
        rule: Severity(level) for level, rules in APPROVED.items() for rule in rules.split()
    }
    assert SEVERITY_OVERRIDES == expected
    assert len(expected) == 110


def test_critical_is_never_assigned():
    # CRITICAL is reserved for correlated findings; a static rule alone cannot justify it.
    assert Severity.CRITICAL not in SEVERITY_OVERRIDES.values()
    assert set(SEVERITY_OVERRIDES.values()) <= {Severity.HIGH, Severity.MEDIUM, Severity.LOW}


def test_tfcr001_privileged_iam_rules_are_high():
    # TFCR001 promotes to CRITICAL only when one of these arrives as HIGH or CRITICAL.
    assert PRIVILEGED_IAM_RULES == {"CKV_AWS_62", "CKV_AWS_63"}
    assert {rule: SEVERITY_OVERRIDES[rule] for rule in PRIVILEGED_IAM_RULES} == {
        "CKV_AWS_62": Severity.HIGH,
        "CKV_AWS_63": Severity.HIGH,
    }


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
                check("CKV_AWS_62"),
                check("CKV_AWS_63", severity={"level": "MEDIUM"}),
                check("CKV_AWS_62", severity="CRITICAL"),
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
        ("CKV_AWS_62", "HIGH", SOURCE),
        ("CKV_AWS_63", "MEDIUM", None),
        ("CKV_AWS_62", "CRITICAL", None),
    ]


@pytest.mark.parametrize("rule", sorted(SEVERITY_OVERRIDES))
def test_default_mapper_leaves_everything_unknown(rule):
    assert SeverityMapper().map(None, rule) == Severity.UNKNOWN
