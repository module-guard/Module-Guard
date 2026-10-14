import pytest
from conftest import FIXTURES

from app.analyzers.source_trust import RULES, SourceTrustAnalyzer


@pytest.mark.parametrize(
    "fixture,rules",
    [
        ("unpinned_git", {"TFSC003", "TFSC006"}),
        ("branch_git", {"TFSC004", "TFSC006"}),
        ("registry_no_version", {"TFSC001"}),
        ("provider_versions", {"TFSC007", "TFSC008", "TFSC009", "TFSC010", "TFSC011"}),
    ],
)
def test_source_trust_fixtures(fixture, rules):
    findings = SourceTrustAnalyzer().analyze(FIXTURES / "source_trust" / fixture)
    assert {f.rule_id for f in findings} == rules
    assert all(f.scanner == "source_trust" and f.line_start and f.guideline for f in findings)
    assert all(f.severity == RULES[f.rule_id][1] for f in findings)


@pytest.mark.parametrize(
    "source,version,rules",
    [
        ("org/module/aws", ">= 1.0", {"TFSC002"}),
        ("org/module/aws", "*", {"TFSC002"}),
        ("org/module/aws", "1.2.3", set()),
        ("org/module/aws", "~> 1.0", set()),
        ("git::https://github.com/org/repo?ref=v1.2.3", None, {"TFSC005", "TFSC006"}),
        ("github.com/org/repo?ref=" + "a" * 40, None, {"TFSC006"}),
        ("github.com/org/repo?ref=unknown-ref", None, {"TFSC006"}),
    ],
)
def test_rule_variants(tmp_path, source, version, rules):
    text = 'module "test" { source = "' + source + '"'
    if version is not None:
        text += '\n version = "' + version + '"'
    (tmp_path / "main.tf").write_text(text + "\n}")
    findings = SourceTrustAnalyzer().analyze(tmp_path)
    assert {f.rule_id for f in findings} == rules


def test_dynamic_version_is_not_misreported_missing(tmp_path):
    (tmp_path / "main.tf").write_text(
        'module "x" { source = "org/repo/aws" version = var.version }'
    )
    assert SourceTrustAnalyzer().analyze(tmp_path) == []
