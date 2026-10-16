import re
from datetime import UTC, datetime

import pytest

from app.models import Finding, Module, Scan
from app.models.enums import ScanStatus, Severity, SourceType
from app.scanners.base import ScanResult
from app.scanners.normalizer import CheckovNormalizer, SeverityMapper
from app.scanners.reapply import reapply
from app.scanners.remediation import GUIDELINE_SOURCE, REMEDIATIONS, has_guideline
from app.scanners.severity_overrides import SEVERITY_OVERRIDES

# The 50 most frequent rules (all scopes) when scanning terraform-aws-modules vpc, eks,
# s3-bucket, rds, iam, security-group and lambda with Checkov 3.3.26. docs/severity-mapping.md
TOP_50 = (
    "CKV_AWS_356 CKV_AWS_111 CKV_TF_1 CKV2_AWS_5 CKV_AWS_394 CKV_AWS_50 CKV_AWS_258 CKV_AWS_338"
    " CKV_AWS_109 CKV_AWS_300 CKV2_AWS_62 CKV_AWS_144 CKV_AWS_21 CKV_AWS_145 CKV_AWS_19"
    " CKV_AWS_353 CKV_AWS_293 CKV_AWS_118 CKV2_AWS_12 CKV_AWS_40 CKV_AWS_157 CKV_AWS_355"
    " CKV_AWS_27 CKV_AWS_304 CKV_AWS_129 CKV_AWS_341 CKV_AWS_79 CKV_AWS_23 CKV_AWS_273"
    " CKV_AWS_16 CKV_AWS_37 CKV_AWS_58 CKV2_AWS_21 CKV2_AWS_14 CKV_AWS_26 CKV_AWS_133"
    " CKV2_AWS_65 CKV_AWS_8 CKV_AWS_88 CKV_AWS_135 CKV_AWS_126 CKV_AWS_39 CKV_AWS_38"
    " CKV2_AWS_41 CKV_AWS_9 CKV_AWS_12 CKV2_AWS_22 CKV_AWS_51 CKV_AWS_163 CKV_AWS_136"
).split()
HANGUL = re.compile(r"[가-힣]")
# A sentence ends with ".", "!" or "?" followed by whitespace or the end of the text.
SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def test_top_rules_and_every_mapped_rule_have_remediation():
    assert len(set(TOP_50)) == 50
    assert set(TOP_50) <= set(REMEDIATIONS)
    assert set(SEVERITY_OVERRIDES) <= set(REMEDIATIONS)
    # Including the TFCR001 allowlist.
    assert {"CKV_AWS_62", "CKV_AWS_63"} <= set(REMEDIATIONS)


@pytest.mark.parametrize("rule", sorted(REMEDIATIONS))
def test_remediation_is_one_or_two_korean_sentences(rule):
    text = REMEDIATIONS[rule]
    assert text == text.strip() and "\n" not in text
    assert HANGUL.search(text), "remediation must be Korean"
    assert 1 <= len(SENTENCE_END.findall(text)) <= 2
    assert text.endswith(".")


@pytest.mark.parametrize(
    "value,expected", [(None, False), ("", False), ("   ", False), (3, False), ("https://x", True)]
)
def test_has_guideline(value, expected):
    assert has_guideline(value) is expected


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


def test_guideline_is_filled_only_when_checkov_left_it_empty(tmp_path):
    findings = (
        CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES))
        .normalize(
            report(
                [
                    check("CKV_AWS_62"),
                    check("CKV_AWS_62", guideline=""),
                    check("CKV_AWS_62", guideline="  "),
                    check("CKV_AWS_62", guideline="https://docs.example/62"),
                    check("CKV_AWS_999"),
                ]
            ),
            tmp_path,
        )
        .findings
    )
    assert [(f.guideline, f.raw_data.get("guideline_source")) for f in findings] == [
        (REMEDIATIONS["CKV_AWS_62"], GUIDELINE_SOURCE),
        (REMEDIATIONS["CKV_AWS_62"], GUIDELINE_SOURCE),
        (REMEDIATIONS["CKV_AWS_62"], GUIDELINE_SOURCE),
        # Checkov's guideline wins and is not relabelled.
        ("https://docs.example/62", None),
        # No text for the rule: left as Checkov sent it.
        (None, None),
    ]
    # Severity provenance is kept alongside.
    assert findings[0].raw_data["severity_source"] == "moduleguard"
    # The Checkov payload itself is preserved; only the provenance key is added.
    assert {k: v for k, v in findings[0].raw_data.items() if not k.endswith("_source")} == check(
        "CKV_AWS_62"
    )


def test_guideline_filling_can_be_disabled(tmp_path):
    (finding,) = (
        CheckovNormalizer(remediations={})
        .normalize(report([check("CKV_AWS_62")]), tmp_path)
        .findings
    )
    assert finding.guideline is None and "guideline_source" not in finding.raw_data


def stored_scan(session, findings):
    module = Module(name="m", source_type=SourceType.GITHUB, source_url="https://github.com/o/m")
    session.add(module)
    session.flush()
    scan = Scan(module_id=module.id, status=ScanStatus.COMPLETED, created_at=datetime.now(UTC))
    session.add(scan)
    session.flush()
    for rule, severity, guideline, raw, scanner in findings:
        session.add(
            Finding(
                scan_id=scan.id,
                rule_id=rule,
                scanner=scanner,
                severity=severity,
                title=rule,
                file_path="/main.tf",
                guideline=guideline,
                raw_data={"check_id": rule, **raw},
            )
        )
    session.commit()
    return scan.id


def test_reapply_fills_and_refreshes_guidelines_of_stored_findings(sessions):
    unknown = Severity.UNKNOWN
    with sessions() as session:
        scan_id = stored_scan(
            session,
            [
                # Stored before this mapping: no severity, no guideline.
                ("CKV_AWS_62", unknown, None, {}, "checkov"),
                # Checkov's own guideline stays.
                ("CKV_AWS_63", unknown, "https://docs.example/63", {}, "checkov"),
                # Our older text is refreshed.
                (
                    "CKV_AWS_21",
                    Severity.LOW,
                    "old",
                    {"guideline_source": "moduleguard", "severity_source": "moduleguard"},
                    "checkov",
                ),
                # Our text for a rule no longer in the table is withdrawn.
                ("CKV_GONE", unknown, "old", {"guideline_source": "moduleguard"}, "checkov"),
                # Correlated findings are never touched.
                ("TFCR001", Severity.HIGH, "correlation text", {}, "correlation"),
            ],
        )
    with sessions() as session:
        (preview,) = reapply(session, dry_run=True)
    assert (preview.severity_changed, preview.guideline_changed) == (2, 3)
    with sessions() as session:
        reapply(session, dry_run=False)
    with sessions() as session:
        rows = {
            f.rule_id: f for f in session.query(Finding).filter(Finding.scan_id == scan_id).all()
        }
    assert rows["CKV_AWS_62"].severity == Severity.HIGH
    assert rows["CKV_AWS_62"].guideline == REMEDIATIONS["CKV_AWS_62"]
    assert rows["CKV_AWS_62"].raw_data == {
        "check_id": "CKV_AWS_62",
        "severity_source": "moduleguard",
        "guideline_source": "moduleguard",
    }
    assert rows["CKV_AWS_63"].severity == Severity.HIGH
    assert rows["CKV_AWS_63"].guideline == "https://docs.example/63"
    assert "guideline_source" not in rows["CKV_AWS_63"].raw_data
    assert rows["CKV_AWS_21"].guideline == REMEDIATIONS["CKV_AWS_21"]
    assert rows["CKV_GONE"].guideline is None and rows["CKV_GONE"].raw_data == {
        "check_id": "CKV_GONE"
    }
    assert (rows["TFCR001"].severity, rows["TFCR001"].guideline) == (
        Severity.HIGH,
        "correlation text",
    )
    with sessions() as session:
        assert [c.changed for c in reapply(session, dry_run=False)] == [0]
