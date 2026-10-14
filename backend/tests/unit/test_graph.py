import pytest
from conftest import FIXTURES

from app.graphs.builder import DependencyGraphBuilder
from app.parsers.domain import EntityType, TerraformEntity, TerraformParseResult
from app.parsers.hcl import HCLTerraformParser
from app.parsers.references import HCLReferenceExtractor


def edge_set(graph):
    addresses = {n.id: n.address for n in graph.nodes}
    return {(addresses[e.source_entity_id], e.edge_type, e.target_address) for e in graph.edges}


def test_dependency_edges():
    parsed = HCLTerraformParser().parse_directory(FIXTURES / "dependency_graph/basic")
    graph = DependencyGraphBuilder().build(parsed)
    edges = edge_set(graph)
    expected = {
        ("root", "CALLS_MODULE", "module.vpc"),
        ("module.vpc", "READS_LOCAL", "local.name"),
        ("local.name", "READS_VARIABLE", "var.region"),
        ("aws_instance.web", "CONSUMES_MODULE_OUTPUT", "module.vpc"),
        ("aws_instance.web", "REFERENCES", "module.vpc.private_subnets"),
        ("aws_instance.web", "READS_DATA", "data.aws_ami.ubuntu"),
        ("aws_instance.web", "USES_PROVIDER", "provider.aws.west"),
        ("module.vpc", "USES_PROVIDER", "provider.aws.west"),
        ("aws_instance.web", "EXPLICIT_DEPENDS_ON", "module.vpc"),
        ("aws_instance.web", "EXPLICIT_DEPENDS_ON", "aws_security_group.web"),
        ("output.instance_id", "RESOURCE_DEPENDS_ON", "aws_instance.web"),
    }
    assert expected <= edges
    assert all(e.target_address != "var.not_a_reference" for e in graph.edges)
    unknown = next(e for e in graph.edges if e.target_address == "var.not_declared")
    assert unknown.target_entity_id is None and not unknown.metadata["resolved"]
    assert any(
        e.expression == "module.vpc.private_subnets[0]" and e.attribute_name == "subnet_id"
        for e in graph.edges
    )
    assert not graph.diagnostics


def test_local_output_resolution():
    graph = DependencyGraphBuilder().build(
        HCLTerraformParser().parse_directory(FIXTURES / "dependency_graph/local_module")
    )
    edges = edge_set(graph)
    assert ("module.network", "CALLS_MODULE", "module.network.module.subnet") in edges
    assert ("output.subnet_id", "REFERENCES", "module.network.output.subnet_id") in edges
    assert (
        "module.network.module.subnet.aws_subnet.private",
        "READS_VARIABLE",
        "module.network.module.subnet.var.cidr",
    ) in edges
    edge = next(e for e in graph.edges if e.target_address == "module.network.output.subnet_id")
    assert edge.target_entity_id is not None


@pytest.mark.parametrize(
    "expression,targets",
    [
        ('"var.fake"', set()),
        ('"hello-${var.region}"', {"var.region"}),
        (
            "var.enabled ? module.a[0].id : data.aws_ami.x.id",
            {"var.enabled", "module.a", "data.aws_ami.x"},
        ),
        ("[for item in var.items : item.id if item.enabled]", {"var.items"}),
        ("{for k,v in var.items : k => v.id}", {"var.items"}),
        ("module.vpc[var.key].ids[0]", {"module.vpc", "var.key"}),
        ("aws_instance.web[*].id", {"aws_instance.web"}),
        ("unknown_function(local.a, var.b)", {"local.a", "var.b"}),
        ('"%{ for x in var.list }${x.name}-${var.region}%{ endfor }"', {"var.list", "var.region"}),
        ('{literal = "module.fake.id", value = local.x}', {"local.x"}),
    ],
)
def test_static_references(expression, targets):
    references = HCLReferenceExtractor().extract("test", expression, "value", [])
    assert {ref.target_address for ref in references} == targets


def test_dynamic_iterator_and_for_each(tmp_path):
    (tmp_path / "main.tf").write_text("""resource "aws_security_group" "web" {
      for_each = var.groups
      dynamic "ingress" {
        for_each = var.rules
        iterator = rule
        content { description = rule.value.name cidr_blocks = var.cidrs }
      }
    }""")
    graph = DependencyGraphBuilder().build(HCLTerraformParser().parse_directory(tmp_path))
    assert {e.target_address for e in graph.edges if e.edge_type == "READS_VARIABLE"} == {
        "var.groups",
        "var.rules",
        "var.cidrs",
    }
    assert not any(e.target_address == "rule.value" for e in graph.edges)


def test_malformed_expression_is_partial_and_raw_preserved():
    entity = TerraformEntity(
        entity_type=EntityType.LOCAL,
        address="local.bad",
        name="bad",
        file_path="main.tf",
        attributes={"value": "var..broken", "other": "var.good"},
    )
    graph = DependencyGraphBuilder().build(TerraformParseResult(entities=[entity]))
    assert graph.diagnostics and graph.nodes[0].attributes["value"] == "var..broken"
    assert any(e.target_address == "var.good" for e in graph.edges)


def test_edge_limit():
    graph = DependencyGraphBuilder(max_edges=1).build(
        HCLTerraformParser().parse_directory(FIXTURES / "dependency_graph/basic")
    )
    assert len(graph.edges) == 1 and graph.diagnostics
