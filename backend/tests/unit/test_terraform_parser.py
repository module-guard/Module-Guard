import json
import shutil
from unittest.mock import patch

import hcl2
import pytest
from conftest import FIXTURES

from app.parsers.hcl import HCLTerraformParser


def test_entities_attributes_and_locations():
    root = FIXTURES / "dependency_graph/basic"
    parsed = HCLTerraformParser().parse_directory(root)
    assert not parsed.diagnostics
    entities = {e.address: e for e in parsed.entities}
    assert set(entities) == {
        "root",
        "required_provider.aws",
        "provider.aws",
        "provider.aws.west",
        "var.region",
        "local.name",
        "module.vpc",
        "data.aws_ami.ubuntu",
        "aws_security_group.web",
        "aws_instance.web",
        "output.instance_id",
    }
    assert entities["module.vpc"].metadata["source"] == "terraform-aws-modules/vpc/aws"
    assert entities["module.vpc"].metadata["version"] == "~> 5.0"
    assert entities["aws_instance.web"].attributes["subnet_id"] == "module.vpc.private_subnets[0]"
    assert entities["required_provider.aws"].metadata["source"] == "hashicorp/aws"
    text = (root / "main.tf").read_text().splitlines()
    for entity in entities.values():
        if entity.line_start is not None:
            assert entity.line_start <= entity.line_end <= len(text)
    assert text[entities["module.vpc"].line_start - 1] == 'module "vpc" {'
    assert (
        entities["root"].metadata["terraform_configurations"][0]["attributes"]["required_version"]
        == '">= 1.0"'
    )


def test_hierarchy_and_single_parse_per_real_file():
    with patch("app.parsers.hcl.hcl2.parses_to_tree", wraps=hcl2.parses_to_tree) as parse:
        result = HCLTerraformParser().parse_directory(FIXTURES / "dependency_graph/local_module")
    assert parse.call_count == 3
    assert result.files_parsed == 3 and result.directories_parsed == 3
    entities = {e.address: e for e in result.entities}
    first = entities["module.network.module.subnet.aws_subnet.private"]
    second = entities["module.copy.module.subnet.aws_subnet.private"]
    assert first.id != second.id
    assert first.metadata["module_path"] == ["network", "subnet"]
    assert first.file_path == "modules/network/subnet/main.tf"
    assert not result.diagnostics


def test_cycle_missing_and_escape(tmp_path):
    (tmp_path / "main.tf").write_text("""module "self" { source = "./" }
module "missing" { source = "./absent" }
module "escape" { source = "../outside" }""")
    parsed = HCLTerraformParser().parse_directory(tmp_path)
    assert {d.code for d in parsed.diagnostics} == {
        "MODULE_CYCLE",
        "MISSING_LOCAL_MODULE",
        "UNSAFE_MODULE_PATH",
    }
    assert parsed.files_parsed == 1


def test_malformed_file_preserves_valid_entities(tmp_path):
    shutil.copy(FIXTURES / "dependency_graph/basic/main.tf", tmp_path / "valid.tf")
    (tmp_path / "broken.tf").write_text('resource "bad" {')
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert result.files_parsed == 1 and result.files_failed == 1
    assert any(e.address == "module.vpc" for e in result.entities)
    assert result.diagnostics[0].code == "FILE_PARSE_FAILED"


def test_symlink_not_read(tmp_path):
    (tmp_path / "main.tf").write_text('module "unsafe" { source = "./link" }')
    outside = tmp_path.parent / (tmp_path.name + "outside")
    outside.mkdir()
    (outside / "secrets.tf").write_text('variable "do_not_read" {}')
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert not any(e.name == "do_not_read" for e in result.entities)
    assert any(d.code == "UNSAFE_MODULE_PATH" for d in result.diagnostics)


def test_limits(tmp_path):
    (tmp_path / "main.tf").write_text("""variable "one" {}
variable "two" {}""")
    assert HCLTerraformParser(max_file_bytes=1).parse_directory(tmp_path).files_failed == 1
    parsed = HCLTerraformParser(max_entities=2).parse_directory(tmp_path)
    assert len(parsed.entities) == 2
    assert parsed.diagnostics[0].code == "ENTITY_LIMIT"


def test_mixed_unquoted_labels(tmp_path):
    (tmp_path / "main.tf").write_text('resource aws_instance "web" { count = 2 }')
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert not result.diagnostics
    assert result.entities[1].address == "aws_instance.web"


def test_address_storage_limit(tmp_path):
    (tmp_path / "main.tf").write_text('variable "' + "x" * 2100 + '" {}')
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert len(result.entities) == 1
    assert result.diagnostics[0].code == "ADDRESS_LIMIT"


def test_json_entities_locations_hierarchy_and_cached_modules():
    result = HCLTerraformParser().parse_directory(FIXTURES / "tf_json/basic")
    assert result.files_parsed == 3 and result.files_failed == 0
    assert result.directories_parsed == 3 and not result.diagnostics
    nodes = {entity.address: entity for entity in result.entities}
    assert {
        "root",
        "var.region",
        "local.name",
        "local.dynamic",
        "provider.aws",
        "provider.aws.west",
        "required_provider.aws",
        "module.network",
        "data.aws_ami.ubuntu",
        "aws_instance.web",
        "output.instance_id",
    } <= nodes.keys()
    assert all(node.line_start is None and node.line_end is None for node in result.entities)
    assert nodes["required_provider.aws"].metadata["source"] == "hashicorp/aws"
    assert nodes["required_provider.aws"].metadata["version"] == "5.1.0"
    assert nodes["var.region"].attributes["type"] == "string"
    assert nodes["module.network"].metadata["source"] == "./modules/network"
    assert nodes["provider.aws.west"].metadata["alias"] == "west"
    first = nodes["module.network.module.subnet.aws_subnet.private"]
    second = nodes["module.copy.module.subnet.aws_subnet.private"]
    assert first.id != second.id
    assert first.metadata["module_path"] == ["network", "subnet"]
    assert first.file_path == "modules/network/subnet/main.tf.json"
    config = nodes["root"].metadata["terraform_configurations"][0]
    assert config["line_start"] is None and config["line_end"] is None
    assert config["attributes"]["required_version"] == '">= 1.0"'


def test_json_and_hcl_mix_in_both_local_module_directions(tmp_path):
    (tmp_path / "main.tf").write_text('module "json" { source = "./json" }')
    (tmp_path / "json").mkdir()
    (tmp_path / "json/main.tf.json").write_text(
        json.dumps({"module": {"hcl": {"source": "../hcl"}}})
    )
    (tmp_path / "hcl").mkdir()
    (tmp_path / "hcl/main.tf").write_text('variable "name" {}')
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert not result.diagnostics and result.files_parsed == 3
    nodes = {node.address: node for node in result.entities}
    assert nodes["module.json"].line_start == 1
    assert nodes["module.json.module.hcl"].line_start is None
    assert nodes["module.json.module.hcl.var.name"].line_start == 1


@pytest.mark.parametrize(
    "text", ["{", "[]", '{"resource": 3}', '{"locals":{"a":1,"a":2}}', '{"locals":{"a":NaN}}']
)
def test_malformed_json_preserves_valid_hcl(tmp_path, text):
    (tmp_path / "valid.tf").write_text('variable "valid" {}')
    (tmp_path / "broken.tf.json").write_text(text)
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert result.files_failed == 1 and result.files_parsed == 1
    assert any(node.address == "var.valid" for node in result.entities)
    assert {diagnostic.code for diagnostic in result.diagnostics} == {"FILE_PARSE_FAILED"}


def test_json_size_entity_and_address_limits(tmp_path):
    path = tmp_path / "main.tf.json"
    path.write_text(json.dumps({"variable": {"one": {}, "two": {}}}))
    assert HCLTerraformParser(max_file_bytes=1).parse_directory(tmp_path).files_failed == 1
    parsed = HCLTerraformParser(max_entities=2).parse_directory(tmp_path)
    assert len(parsed.entities) == 2 and parsed.diagnostics[0].code == "ENTITY_LIMIT"
    path.write_text(json.dumps({"variable": {"x" * 2100: {}}}))
    assert HCLTerraformParser().parse_directory(tmp_path).diagnostics[0].code == "ADDRESS_LIMIT"


def test_json_symlinks_escape_exclusions_cycle_and_depth(tmp_path):
    outside = tmp_path.parent / (tmp_path.name + "-outside")
    outside.mkdir()
    (outside / "secret.tf.json").write_text('{"variable":{"do_not_read":{}}}')
    (tmp_path / "secret.tf.json").symlink_to(outside / "secret.tf.json")
    (tmp_path / "link").symlink_to(outside, target_is_directory=True)
    (tmp_path / ".terraform").mkdir()
    (tmp_path / ".terraform/secret.tf.json").write_text('{"variable":{"do_not_read":{}}}')
    (tmp_path / "child").mkdir()
    (tmp_path / "child/main.tf.json").write_text('{"variable":{"child":{}}}')
    (tmp_path / "main.tf.json").write_text(
        json.dumps(
            {
                "module": {
                    "escape": {"source": "../" + outside.name},
                    "link": {"source": "./link"},
                    "excluded": {"source": "./.terraform"},
                    "cycle": {"source": "./"},
                    "missing": {"source": "./absent"},
                    "child": {"source": "./child"},
                }
            }
        )
    )
    result = HCLTerraformParser(max_depth=0).parse_directory(tmp_path)
    assert not any(
        node.name in {"do_not_read", "child"} and node.entity_type == "VARIABLE"
        for node in result.entities
    )
    assert {d.code for d in result.diagnostics} == {
        "UNSAFE_PATH",
        "UNSAFE_MODULE_PATH",
        "EXCLUDED_MODULE_PATH",
        "MODULE_CYCLE",
        "DEPTH_LIMIT",
    }
    # Missing is checked after depth, as in the existing HCL policy.
    assert "MISSING_LOCAL_MODULE" in {
        d.code for d in HCLTerraformParser().parse_directory(tmp_path).diagnostics
    }


def test_json_unsupported_blocks_are_diagnosed_without_discarding_supported_ones(tmp_path):
    (tmp_path / "main.tf.json").write_text(
        json.dumps(
            {
                "variable": {"valid": {}},
                "check": {"unknown": {}},
                "resource": {"aws_instance": {"web": {"dynamic": {"ingress": {"content": {}}}}}},
            }
        )
    )
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert result.files_parsed == 1 and result.files_failed == 0
    assert {d.code for d in result.diagnostics} == {
        "UNSUPPORTED_JSON_BLOCK",
        "UNSUPPORTED_JSON_DYNAMIC",
    }
    assert {node.address for node in result.entities} == {"root", "var.valid", "aws_instance.web"}


def test_json_general_block_arrays_and_legacy_required_provider(tmp_path):
    (tmp_path / "main.tf.json").write_text(
        json.dumps(
            {
                "resource": [{"aws_instance": [{"a": [{}]}, {"b": {}}]}],
                "terraform": [{"required_providers": [{"aws": "5.1.0"}]}],
                "locals": [{"first": 1}, {"second": True}],
            }
        )
    )
    result = HCLTerraformParser().parse_directory(tmp_path)
    assert not result.diagnostics
    nodes = {node.address: node for node in result.entities}
    assert {
        "aws_instance.a",
        "aws_instance.b",
        "local.first",
        "local.second",
        "required_provider.aws",
    } <= nodes.keys()
    assert nodes["required_provider.aws"].metadata["version"] == "5.1.0"
    assert nodes["local.second"].attributes["value"] == "true"
