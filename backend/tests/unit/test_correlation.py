from uuid import uuid4

import pytest
from conftest import FIXTURES

from app.core.config import Settings
from app.correlation.context import CorrelationContext
from app.correlation.domain import Confidence, PathQuality
from app.correlation.engine import CorrelationEngine
from app.correlation.matcher import FindingEntityMatcher
from app.correlation.rules import CorrelationRule, FlowCorrelationRule, ProviderCorrelationRule
from app.correlation.scoring import path_quality
from app.schemas.finding import NormalizedFinding
from app.services.analysis_service import StaticAnalysisService


def checkov(resource="aws_iam_role_policy.admin", severity="HIGH", rule="CKV_AWS_62", **kwargs):
    return NormalizedFinding(
        rule_id=rule,
        scanner="checkov",
        severity=severity,
        title="Privileged IAM configuration",
        resource=resource,
        **kwargs,
    )


def context(fixture, findings=None, settings=None):
    static = StaticAnalysisService().run(FIXTURES / "correlation" / fixture, uuid4())
    return CorrelationContext(uuid4(), static.graph, static.findings + (findings or []), settings)


@pytest.mark.parametrize(
    "rule,fixture,findings",
    [
        ("TFCR001", "mutable_git_to_iam", [checkov()]),
        ("TFCR002", "mutable_git_to_user_data", []),
        ("TFCR003", "secret_to_user_data", []),
        ("TFCR004", "secret_to_environment", []),
        ("TFCR005", "mutable_git_to_iam", [checkov()]),
        ("TFCR006", "pinned_external_to_sensitive", []),
        ("TFCR007", "broad_provider", [checkov()]),
        ("TFCR008", "opaque_external_to_sink", []),
    ],
)
def test_each_rule_positive(rule, fixture, findings):
    ctx = context(fixture, findings)
    engine = CorrelationEngine(
        [ProviderCorrelationRule() if rule == "TFCR007" else FlowCorrelationRule(rule)]
    )
    result = engine.evaluate(ctx)
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == rule and finding.scanner == "correlation"
    assert finding.raw_data["flow_edge_ids"] or finding.raw_data["dependency_edge_ids"]
    assert finding.raw_data["attack_path"]
    assert finding.raw_data["confidence"] in {"HIGH", "MEDIUM"}


@pytest.mark.parametrize(
    "rule,fixture,findings",
    [
        ("TFCR001", "unrelated_findings", [checkov()]),
        ("TFCR002", "pinned_external_to_sensitive", []),
        ("TFCR003", "unrelated_findings", [checkov()]),
        ("TFCR004", "secret_to_user_data", []),
        ("TFCR005", "unrelated_findings", [checkov()]),
        ("TFCR006", "mutable_git_to_iam", []),
        ("TFCR007", "broad_provider", [checkov(severity="UNKNOWN")]),
        ("TFCR008", "pinned_external_to_sensitive", []),
    ],
)
def test_each_rule_negative(rule, fixture, findings):
    engine = CorrelationEngine(
        [ProviderCorrelationRule() if rule == "TFCR007" else FlowCorrelationRule(rule)]
    )
    assert engine.evaluate(context(fixture, findings)).findings == []


def test_priority_evidence_ids_and_scoring():
    original = checkov()
    result = CorrelationEngine().evaluate(context("mutable_git_to_iam", [original]))
    assert len(result.findings) == 1
    finding = result.findings[0]
    assert finding.rule_id == "TFCR001" and finding.severity == "CRITICAL"
    assert finding.raw_data["related_finding_ids"] == [str(original.id)]
    assert finding.raw_data["confidence"] == "MEDIUM"
    assert finding.raw_data["path_quality"] == "OPAQUE"
    assert set(finding.raw_data["suppressed_rule_ids"]) == {"TFCR005", "TFCR008"}
    assert finding.raw_data["score"]["total"] >= 11
    base = CorrelationEngine().evaluate(context("mutable_git_to_iam"))
    assert base.findings[0].severity == "HIGH"


def test_dedup_different_paths():
    result = CorrelationEngine().evaluate(context("duplicate_paths", [checkov(), checkov()]))
    assert len(result.findings) == 1
    assert result.summary.suppressed >= 1
    assert len(result.findings[0].raw_data["flow_edge_ids"]) == 2


def test_unrelated_and_mismatched_checkov_resource():
    assert CorrelationEngine().evaluate(context("unrelated_findings", [checkov()])).findings == []
    result = CorrelationEngine([FlowCorrelationRule("TFCR005")]).evaluate(
        context("mutable_git_to_iam", [checkov(resource="module.other.aws_iam_role_policy.admin")])
    )
    assert result.findings == []


def test_attribute_mismatch_does_not_join_or_escalate():
    result = CorrelationEngine().evaluate(
        context(
            "mutable_git_to_iam", [checkov(raw_data={"check_result": {"evaluated_keys": ["role"]}})]
        )
    )
    assert result.findings[0].severity == "HIGH"
    assert result.findings[0].raw_data["related_finding_ids"] == []


def test_attribute_match_keeps_explicit_evidence():
    result = CorrelationEngine().evaluate(
        context(
            "mutable_git_to_iam",
            [
                checkov(
                    raw_data={
                        "check_result": {"evaluated_keys": ["policy/[0]/Statement/[0]/Action"]}
                    }
                )
            ],
        )
    )
    assert result.findings[0].raw_data["checkov_mappings"][0]["level"] == "attribute"


def test_secret_environment_is_potential_not_leak():
    result = CorrelationEngine().evaluate(context("secret_to_environment"))
    finding = result.findings[0]
    assert finding.rule_id == "TFCR004" and finding.severity == "MEDIUM"
    assert finding.raw_data["confidence"] == "HIGH"
    assert "potential secret exposure" in finding.description


def test_unresolved_and_partial_paths_are_suppressed():
    for alteration in ("unresolved", "collection", "field_projection"):
        ctx = context("mutable_git_to_iam", [checkov()])
        edge = ctx.flows[-1]
        if alteration == "unresolved":
            edge.metadata["target"]["resolved"] = False
            edge.metadata["target"]["reason"] = "undeclared_reference"
        elif alteration == "collection":
            edge.metadata["collection_iteration"] = True
        else:
            edge.metadata["referenced_attribute"] = "untracked_field"
        assert CorrelationEngine().evaluate(ctx).findings == []


def test_resolved_path_quality():
    ctx = context("secret_to_user_data")
    ctx.prepare()
    assert ctx.candidates[0].quality == PathQuality.RESOLVED
    assert path_quality(ctx.candidates[0].path) == PathQuality.RESOLVED


def test_mapping_exact_nested_ambiguous_and_wrong_scope(tmp_path):
    (tmp_path / "child").mkdir()
    (tmp_path / "main.tf").write_text(
        'module "a" { source = "./child" }\nmodule "b" { source = "./child" }'
    )
    (tmp_path / "child/main.tf").write_text(
        'resource "aws_iam_role_policy" "admin" { policy = "{}" }'
    )
    parsed = StaticAnalysisService().run(tmp_path, uuid4()).graph.nodes
    matcher = FindingEntityMatcher(parsed)
    assert (
        matcher.checkov(checkov(resource="module.a.aws_iam_role_policy.admin")).confidence
        == Confidence.HIGH
    )
    assert matcher.checkov(checkov(file_path="child/main.tf", line_start=1, line_end=1)) is None
    assert (
        matcher.checkov(
            checkov(resource="module.c.aws_iam_role_policy.admin", file_path="child/main.tf")
        )
        is None
    )
    assert (
        matcher.checkov(
            checkov(resource="module.a.aws_iam_role_policy.admin", file_path="wrong.tf")
        )
        is None
    )
    matcher = FindingEntityMatcher([e for e in parsed if not e.address.startswith("module.b")])
    match = matcher.checkov(checkov(file_path="/child/main.tf", line_start=1, line_end=1))
    assert match.entity.address == "module.a.aws_iam_role_policy.admin"
    assert match.confidence == Confidence.MEDIUM
    assert (
        matcher.checkov(checkov(resource='module.a["key"].aws_iam_role_policy.admin[0]')).confidence
        == Confidence.HIGH
    )


def test_source_trust_exact_entity_id_and_module_path():
    ctx = context("mutable_git_to_iam")
    trust = ctx.findings_by_rule["TFSC004"][0]
    assert ctx.matcher.source_trust(trust).address == "module.policy_source"
    wrong = trust.model_copy(deep=True)
    wrong.raw_data["module_path"] = ["other"]
    assert ctx.matcher.source_trust(wrong) is None
    wrong = trust.model_copy(deep=True)
    wrong.raw_data["entity_id"] = str(uuid4())
    assert ctx.matcher.source_trust(wrong) is None


def test_secret_metadata_attribute_is_not_secret_value(tmp_path):
    (tmp_path / "main.tf").write_text(
        'data "aws_ssm_parameter" "secret" { name = "secret" }\nresource "aws_instance" "web" { user_data = data.aws_ssm_parameter.secret.name }'
    )
    result = StaticAnalysisService().run(tmp_path, uuid4())
    assert (
        CorrelationEngine()
        .evaluate(CorrelationContext(uuid4(), result.graph, result.findings))
        .findings
        == []
    )


def test_tag_is_not_mutable_branch(tmp_path):
    text = (
        (FIXTURES / "correlation/mutable_git_to_user_data/main.tf")
        .read_text()
        .replace("ref=main", "ref=v1.2.3")
    )
    (tmp_path / "main.tf").write_text(text)
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(uuid4(), static.graph, static.findings)
    )
    assert [f.rule_id for f in result.findings] == ["TFCR008"]
    assert result.findings[0].severity == "HIGH"


def test_search_budget_and_rule_failure_isolation():
    settings = Settings(max_flow_search_steps=1, _env_file=None)
    result = CorrelationEngine().evaluate(context("mutable_git_to_iam", settings=settings))
    assert result.summary.truncated and not result.findings

    class BrokenRule(CorrelationRule):
        rule_id = "BROKEN"

        def evaluate(self, context):
            raise RuntimeError("private literal")

    result = CorrelationEngine([BrokenRule(), FlowCorrelationRule("TFCR001")]).evaluate(
        context("mutable_git_to_iam")
    )
    assert len(result.findings) == 1 and result.summary.rule_errors == ["BROKEN"]
    assert "private literal" not in str(result)


def test_wrong_provider_cannot_correlate(tmp_path):
    (tmp_path / "main.tf").write_text("""terraform {
 required_providers {
  aws = { source = "hashicorp/aws", version = ">= 1.0" }
  azurerm = { source = "hashicorp/azurerm", version = "3.0.0" }
 }
}
resource "azurerm_storage_account" "example" {}
""")
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(
            uuid4(),
            static.graph,
            static.findings + [checkov(resource="azurerm_storage_account.example")],
        )
    )
    assert not result.findings


def test_cross_module_flow_and_trust_are_scoped(tmp_path):
    (tmp_path / "child").mkdir()
    (tmp_path / "main.tf").write_text(
        'module "a" { source = "./child" }\nmodule "b" { source = "./child" }'
    )
    (
        tmp_path / "child/main.tf"
    ).write_text("""module "external" { source = "git::https://github.com/org/policy.git?ref=main" }
resource "aws_iam_role_policy" "admin" { policy = module.external.policy }
""")
    static = StaticAnalysisService().run(tmp_path, uuid4())
    ctx = CorrelationContext(
        uuid4(),
        static.graph,
        static.findings + [checkov(resource="module.a.aws_iam_role_policy.admin")],
    )
    result = CorrelationEngine([FlowCorrelationRule("TFCR005")]).evaluate(ctx)
    assert len(result.findings) == 1
    assert result.findings[0].raw_data["source"] == "module.a.module.external.policy"
    assert result.findings[0].resource == "module.a.aws_iam_role_policy.admin"


def test_only_resource_level_unrelated_checkov_does_not_privilege_escalate():
    result = CorrelationEngine().evaluate(
        context("mutable_git_to_iam", [checkov(rule="CKV_TEST_UNRELATED")])
    )
    assert result.findings[0].rule_id == "TFCR001" and result.findings[0].severity == "HIGH"


def test_generic_mutable_rule_resource_sink_without_sensitive_classifier(tmp_path):
    (
        tmp_path / "main.tf"
    ).write_text("""module "external" { source = "git::https://github.com/org/config.git" }
resource "aws_s3_bucket" "example" { bucket = module.external.name }
""")
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(
            uuid4(),
            static.graph,
            static.findings
            + [checkov(resource="aws_s3_bucket.example", rule="CKV_TEST", severity="HIGH")],
        )
    )
    assert len(result.findings) == 1 and result.findings[0].rule_id == "TFCR005"
    assert result.findings[0].raw_data["checkov_mappings"][0]["level"] == "resource"


def test_correlation_cycle_safe_and_depth_limited(tmp_path):
    (tmp_path / "main.tf").write_text("""data "aws_ssm_parameter" "secret" { name = "secret" }
locals { a = "${data.aws_ssm_parameter.secret.value}${local.b}"\n b = local.a }
resource "aws_instance" "web" { user_data = local.b }
""")
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(uuid4(), static.graph, static.findings)
    )
    assert len(result.findings) == 1
    path = result.findings[0].raw_data["flow_path"]
    assert len(path) == len(set(path))
    shallow = CorrelationContext(
        uuid4(), static.graph, static.findings, Settings(max_flow_depth=1, _env_file=None)
    )
    result = CorrelationEngine().evaluate(shallow)
    assert not result.findings and result.summary.truncated


def test_cross_module_input_trace_is_reused(tmp_path):
    (tmp_path / "child").mkdir()
    (
        tmp_path / "main.tf"
    ).write_text("""module "external" { source = "git::https://github.com/org/policy.git?ref=main" }
module "iam" { source = "./child"\n policy = module.external.policy }
""")
    (tmp_path / "child/main.tf").write_text("""variable "policy" {}
resource "aws_iam_role_policy" "admin" { policy = var.policy }
""")
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(
            uuid4(),
            static.graph,
            static.findings + [checkov(resource="module.iam.aws_iam_role_policy.admin")],
        )
    )
    assert len(result.findings) == 1
    assert result.findings[0].raw_data["flow_path"] == [
        "module.external.policy",
        "module.iam.var.policy",
        "module.iam.aws_iam_role_policy.admin.policy",
    ]


def test_location_only_mapping_does_not_escalate_critical(tmp_path):
    (tmp_path / "child").mkdir()
    (tmp_path / "main.tf").write_text('module "child" { source = "./child" }')
    (tmp_path / "child/main.tf").write_text(
        (FIXTURES / "correlation/mutable_git_to_iam/main.tf").read_text()
    )
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(
            uuid4(), static.graph, static.findings + [checkov(file_path="child/main.tf")]
        )
    )
    assert result.findings[0].severity == "HIGH"
    assert result.findings[0].raw_data["checkov_mappings"][0]["confidence"] == "MEDIUM"


def test_unresolved_external_module_reference_is_not_trust_evidence(tmp_path):
    (tmp_path / "main.tf").write_text(
        'resource "aws_iam_role_policy" "admin" { policy = module.missing.policy }'
    )
    static = StaticAnalysisService().run(tmp_path, uuid4())
    result = CorrelationEngine().evaluate(
        CorrelationContext(uuid4(), static.graph, static.findings + [checkov()])
    )
    assert result.findings == []
