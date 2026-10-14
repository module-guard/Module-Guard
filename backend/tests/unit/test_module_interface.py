from pathlib import Path

from app.analyzers.module_interface import detect_license, extract_interface, read_readme
from app.parsers.hcl import HCLTerraformParser

FIXTURES = Path(__file__).parents[1] / "fixtures"

MODULE = """
variable "name" {
  description = "Name of the VPC"
  type        = string
}

variable "cidr" {
  type    = string
  default = "10.0.0.0/16"
}

variable "tags" {
  description = <<-EOT
    Extra tags.
      Indented detail.
  EOT
  type        = map(string)
  default     = {}
}

variable "token" {
  type      = string
  sensitive = true
}

variable "untyped" {}

output "vpc_id" {
  description = "The VPC id"
  value       = "x"
}

output "secret" {
  value     = var.token
  sensitive = true
}
"""


def parse(root):
    return HCLTerraformParser().parse_directory(root).entities


def test_variables_and_outputs(tmp_path):
    (tmp_path / "main.tf").write_text(MODULE)
    variables, outputs = extract_interface(parse(tmp_path))
    by_name = {v["name"]: v for v in variables}
    assert [v["name"] for v in variables] == ["cidr", "name", "tags", "token", "untyped"]
    assert by_name["name"] == {
        "name": "name",
        "description": "Name of the VPC",
        "sensitive": False,
        "type": "string",
        "default": None,
        "required": True,
    }
    assert by_name["cidr"]["default"] == '"10.0.0.0/16"' and not by_name["cidr"]["required"]
    assert by_name["tags"]["description"] == "Extra tags.\n  Indented detail."
    assert by_name["tags"]["type"] == "map(string)" and by_name["tags"]["default"] == "{}"
    assert by_name["token"]["sensitive"] and by_name["token"]["required"]
    assert by_name["untyped"]["type"] is None and by_name["untyped"]["required"]
    assert outputs == [
        {"name": "secret", "description": None, "sensitive": True},
        {"name": "vpc_id", "description": "The VPC id", "sensitive": False},
    ]


def test_child_module_declarations_are_excluded():
    root = FIXTURES / "dependency_graph" / "local_module"
    entities = parse(root)
    # The child module declares variable "region"; the root declares only an output.
    assert any(e.name == "region" and e.metadata.get("module_path") for e in entities)
    variables, outputs = extract_interface(entities)
    assert variables == [] and [o["name"] for o in outputs] == ["subnet_id"]


def test_readme_and_license(tmp_path):
    assert read_readme(tmp_path) is None and detect_license(tmp_path) is None
    (tmp_path / "ReadMe.md").write_text("# Title\n")
    (tmp_path / "LICENSE").write_text("\n   Apache License\n   Version 2.0, January 2004\n")
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "README.md").write_text("nested")
    assert read_readme(tmp_path) == "# Title\n"
    assert detect_license(tmp_path) == "Apache-2.0"
    (tmp_path / "LICENSE").write_text("Permission is hereby granted, free of charge, to any")
    assert detect_license(tmp_path) == "MIT"
    (tmp_path / "LICENSE").write_text("All rights reserved.")
    assert detect_license(tmp_path) is None


def test_provider_comes_from_root_required_providers(tmp_path):
    from app.analyzers.module_interface import extract_provider

    assert extract_provider(parse(FIXTURES / "dependency_graph" / "local_module")) is None
    (tmp_path / "versions.tf").write_text(
        "terraform {\n  required_providers {\n"
        '    random = { source = "hashicorp/random" }\n'
        '    google = { source = "hashicorp/google", version = ">= 5.0" }\n'
        '    tls = { source = "hashicorp/tls" }\n  }\n}\n'
    )
    (tmp_path / "child").mkdir()
    (tmp_path / "child" / "versions.tf").write_text(
        'terraform {\n  required_providers {\n    aws = { source = "hashicorp/aws" }\n  }\n}\n'
    )
    (tmp_path / "main.tf").write_text('module "child" {\n  source = "./child"\n}\n')
    # Helper providers and child modules do not decide where the module deploys.
    assert extract_provider(parse(tmp_path)) == "google"
    (tmp_path / "versions.tf").write_text(
        'terraform {\n  required_providers {\n    random = { source = "hashicorp/random" }\n  }\n}\n'
    )
    assert extract_provider(parse(tmp_path)) == "random"
