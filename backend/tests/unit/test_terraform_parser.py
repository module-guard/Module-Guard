import shutil
from unittest.mock import patch

import hcl2
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
