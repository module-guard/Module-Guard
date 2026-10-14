import pytest
from conftest import FIXTURES

from app.analyzers.data_flow import DataFlowAnalyzer
from app.analyzers.flow_classifiers import (
    FlowSinkClassifier,
    FlowSourceClassifier,
    SensitiveSinkRule,
)
from app.graphs.builder import DependencyGraphBuilder
from app.graphs.flow_builder import DataFlowGraphBuilder
from app.parsers.domain import EntityType, TerraformEntity
from app.parsers.hcl import HCLTerraformParser


def analyze(name):
    return DataFlowAnalyzer().analyze(FIXTURES / "data_flow" / name)


def pairs(result):
    return {(e.metadata["source_address"], e.target_address, e.edge_type) for e in result.edges}


def test_basic_flow_direction_and_transformations():
    result = analyze("basic")
    assert pairs(result) == {
        ("var.bootstrap", "local.encoded", "PROPAGATES_THROUGH_LOCAL"),
        ("local.encoded", "aws_instance.web.user_data", "SINKS_TO_RESOURCE_ATTRIBUTE"),
    }
    edge = result.edges[0]
    assert edge.metadata["transformations"] == ["base64encode"]
    assert edge.metadata["source"]["file_path"] == "main.tf"
    assert edge.metadata["source"]["line_start"] == 1
    assert result.edges[1].metadata["sink_category"] == "CODE_EXECUTION"


@pytest.mark.parametrize(
    ("fixture", "source", "target"),
    [
        ("local_chain", "local.first", "local.second"),
        ("module_input", "local.encoded", "module.compute.var.user_data"),
        (
            "module_input",
            "module.compute.var.user_data",
            "module.compute.aws_instance.web.user_data",
        ),
        (
            "module_output",
            "module.compute.aws_instance.web.id",
            "module.compute.output.instance_id",
        ),
        ("module_output", "module.compute.output.instance_id", "module.compute.instance_id"),
        ("module_output", "module.compute.instance_id", "aws_eip.ip.instance"),
        ("module_output", "module.compute.instance_id", "output.alias"),
        ("nested_module", "module.a.var.input", "module.a.module.b.var.input"),
        (
            "nested_module",
            "module.a.module.b.var.input",
            "module.a.module.b.aws_instance.web.user_data",
        ),
        ("data_source", "data.aws_ssm_parameter.secret.value", "aws_instance.web.user_data"),
        (
            "resource_reference",
            "aws_security_group.web.id",
            "aws_instance.web.vpc_security_group_ids",
        ),
    ],
)
def test_value_edges(fixture, source, target):
    assert any(s == source and t == target for s, t, _ in pairs(analyze(fixture)))


def test_conditional_and_collection():
    result = analyze("conditional")
    edges = [e for e in result.edges if e.target_address == "local.subnet"]
    assert len(edges) == 3
    assert all(e.metadata["conditional"] for e in edges)
    collection = [e for e in result.edges if e.target_address == "local.subnets"]
    assert len(collection) == 1
    assert collection[0].metadata["source_address"] == "module.vpc.private_subnets"
    assert collection[0].metadata["traversal"].endswith("[0]")


def test_external_boundary():
    result = analyze("external_module")
    assert result.summary.flows == result.summary.opaque_external_flows == 2
    assert result.summary.unresolved_flows == 2
    for edge in result.edges:
        assert edge.metadata["external"] and edge.metadata["opaque"]
        assert not edge.metadata["resolved"]
    output = next(e for e in result.edges if e.edge_type == "SINKS_TO_RESOURCE_ATTRIBUTE")
    assert output.metadata["source"]["source_category"] == "MODULE_OUTPUT"
    assert output.metadata["source"]["reason"] == "external_module"


def analyze_text(tmp_path, text):
    (tmp_path / "main.tf").write_text(text)
    return DataFlowAnalyzer().analyze(tmp_path)


def test_unresolved_source_has_no_fake_owner(tmp_path):
    result = analyze_text(tmp_path, 'resource "aws_instance" "web" { user_data = var.missing }')
    assert len(result.edges) == 1
    assert result.edges[0].source_entity_id is None
    assert result.edges[0].metadata["source_address"] == "var.missing"
    assert result.summary.unresolved_flows == 1


def test_object_aggregate_preserves_path(tmp_path):
    result = analyze_text(
        tmp_path,
        """variable "region" {}
variable "name" {}
locals { config = { region = var.region, name = var.name } }
resource "aws_instance" "web" { user_data = local.config.name }
""",
    )
    assert len(result.edges) == 3
    last = result.edges[-1]
    assert last.metadata["source_address"] == "local.config"
    assert last.metadata["referenced_attribute"] == "name"
    assert last.metadata["object_precision"] == "aggregate"


def test_for_expression_conservative_and_diagnostic(tmp_path):
    result = analyze_text(
        tmp_path,
        """resource "aws_subnet" "example" {}
locals { ids = [for subnet in aws_subnet.example : subnet.id] }
""",
    )
    assert any(s == "aws_subnet.example" and t == "local.ids" for s, t, _ in pairs(result))
    assert any(d["code"] == "COLLECTION_FLOW_AGGREGATED" for d in result.diagnostics)
    assert not any("subnet.id" == s for s, _, _ in pairs(result))


def test_no_literals_meta_arguments_or_dependency_direction(tmp_path):
    result = analyze_text(
        tmp_path,
        """variable "x" {}
locals { literal = "var.fake" }
resource "aws_instance" "web" {
 user_data = unknown_function(var.x)
 depends_on = [aws_instance.other]
 count = var.x
}
resource "aws_instance" "other" {}
""",
    )
    assert len(result.edges) == 1
    assert result.edges[0].metadata["transformations"] == ["unknown_function"]


def test_cycle_does_not_recurse_in_builder(tmp_path):
    result = analyze_text(tmp_path, "locals { a = local.b\n b = local.a }")
    assert len(result.edges) == 2


def test_flow_limit():
    parsed = HCLTerraformParser().parse_directory(FIXTURES / "data_flow/module_input")
    result = DataFlowGraphBuilder(max_edges=1).build(DependencyGraphBuilder().build(parsed))
    assert len(result.edges) == 1
    assert [d["code"] for d in result.diagnostics] == ["FLOW_EDGE_LIMIT"]


@pytest.mark.parametrize(
    ("kind", "resource", "expected"),
    [
        ("VARIABLE", None, "USER_INPUT"),
        ("LOCAL", None, "LOCAL_VALUE"),
        ("MODULE", None, "MODULE_OUTPUT"),
        ("OUTPUT", None, "MODULE_OUTPUT"),
        ("RESOURCE", "aws_instance", "RESOURCE_ATTRIBUTE"),
        ("DATA", "aws_ami", "DATA_SOURCE"),
        *[
            ("DATA", r, "SECRET_SOURCE")
            for r in (
                "aws_ssm_parameter",
                "aws_secretsmanager_secret",
                "aws_secretsmanager_secret_version",
                "azurerm_key_vault_secret",
                "google_secret_manager_secret_version",
            )
        ],
    ],
)
def test_source_classification(kind, resource, expected):
    entity = TerraformEntity(
        entity_type=EntityType(kind),
        address="x",
        name="x",
        file_path="main.tf",
        resource_type=resource,
    )
    assert FlowSourceClassifier().classify(entity) == expected
    assert FlowSourceClassifier().classify(None) == "UNKNOWN"


@pytest.mark.parametrize(
    ("resource", "attribute", "expected"),
    [
        ("aws_instance", "user_data", "CODE_EXECUTION"),
        ("aws_launch_template", "user_data", "CODE_EXECUTION"),
        ("aws_iam_policy", "policy", "IAM_POLICY"),
        ("aws_iam_role_policy", "policy", "IAM_POLICY"),
        ("aws_lambda_function", "environment[0].variables", "ENVIRONMENT"),
        ("aws_ecs_task_definition", "container_definitions", "COMMAND"),
        ("aws_ssm_parameter", "value", "SECRET_STORAGE"),
        ("aws_instance", "ami", "UNKNOWN"),
    ],
)
def test_sink_classification(resource, attribute, expected):
    entity = TerraformEntity(
        entity_type=EntityType.RESOURCE,
        address="x",
        name="x",
        file_path="main.tf",
        resource_type=resource,
    )
    assert FlowSinkClassifier().classify(entity, attribute) == expected
    custom = FlowSinkClassifier((SensitiveSinkRule(resource, "custom", "NETWORK"),))
    assert custom.classify(entity, "custom") == "NETWORK"


def test_trace_cycle_depth_path_and_work_limits(tmp_path):
    from unittest.mock import Mock
    from uuid import uuid4

    from app.core.config import Settings
    from app.schemas.flow import FlowEdgeResponse
    from app.services.flow_service import FlowService

    result = analyze_text(
        tmp_path,
        """variable "input" {}
locals {
 a = "${var.input}-${local.b}"
 b = local.a
 c = local.a
}
resource "aws_instance" "web" { user_data = "${local.b}-${local.c}" }
""",
    )
    service = FlowService.__new__(FlowService)
    service.settings = Settings(_env_file=None)
    service.load = Mock(
        return_value=[
            FlowEdgeResponse(
                id=e.id,
                source=e.metadata["source_address"],
                target=e.target_address,
                type=e.edge_type,
                expression=e.expression,
                metadata=e.metadata,
            )
            for e in result.edges
        ]
    )
    traced = service.trace(uuid4(), "var.input", "aws_instance.web.user_data")
    assert traced.found and len(traced.paths) == 2
    assert all(len({n.address for n in path}) == len(path) for path in traced.paths)
    assert not service.trace(uuid4(), "aws_instance.web.user_data", "var.input").found
    service.settings.max_flow_paths = 1
    traced = service.trace(uuid4(), "var.input", "aws_instance.web.user_data")
    assert len(traced.paths) == 1 and traced.truncated
    service.settings.max_flow_search_steps = 1
    traced = service.trace(uuid4(), "var.input", "aws_instance.web.user_data")
    assert not traced.found and traced.truncated and traced.explored_steps == 1


def test_duplicate_local_calls_keep_distinct_addresses(tmp_path):
    (tmp_path / "child").mkdir()
    (tmp_path / "child/main.tf").write_text('variable "x" {}\noutput "x" { value = var.x }')
    result = analyze_text(
        tmp_path,
        """variable "x" {}
module "a" { source = "./child"\n x = var.x }
module "b" { source = "./child"\n x = var.x }
""",
    )
    targets = {t for _, t, _ in pairs(result)}
    assert {"module.a.var.x", "module.b.var.x", "module.a.x", "module.b.x"} <= targets


def test_splat_and_index_normalize_resource_attribute(tmp_path):
    result = analyze_text(
        tmp_path,
        """resource "aws_subnet" "example" {}
locals { ids = aws_subnet.example[*].id }
""",
    )
    assert ("aws_subnet.example.id", "local.ids", "PROPAGATES_THROUGH_LOCAL") in pairs(result)


def test_dynamic_iterator_is_diagnostic_without_invented_source(tmp_path):
    result = analyze_text(
        tmp_path,
        """variable "rules" {}
resource "aws_security_group" "web" {
 dynamic "ingress" {
  for_each = var.rules
  content { cidr_blocks = ingress.value.cidrs }
 }
}
""",
    )
    assert any(d["code"] == "DYNAMIC_ITERATOR_FLOW_UNRESOLVED" for d in result.diagnostics)
    assert not any(s.startswith("ingress") for s, _, _ in pairs(result))


def test_nested_attribute_indexes_align_with_reference(tmp_path):
    result = analyze_text(
        tmp_path,
        """variable "encrypted" {}
resource "aws_instance" "web" {
 root_block_device { encrypted = var.encrypted }
}
output "encrypted" { value = aws_instance.web.root_block_device[0].encrypted }
""",
    )
    assert (
        "var.encrypted",
        "aws_instance.web.root_block_device.encrypted",
        "SINKS_TO_RESOURCE_ATTRIBUTE",
    ) in pairs(result)
    assert (
        "aws_instance.web.root_block_device.encrypted",
        "output.encrypted",
        "PASSES_TO_OUTPUT",
    ) in pairs(result)
    assert result.edges[0].attribute_name == "root_block_device[0].encrypted"
