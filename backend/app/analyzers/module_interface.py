"""Root-module interface and documentation derived from already-parsed entities and files."""

import re
from pathlib import Path
from typing import Any

from app.parsers.domain import EntityType, TerraformEntity
from app.parsers.hcl import literal_string

README_LIMIT_BYTES = 200 * 1024
HEREDOC = re.compile(r"^<<-?\s*(\w+)\s*\n(.*)\n\s*\1\s*$", re.DOTALL)
# Distinctive phrases near the top of each license text, checked in order.
LICENSE_MARKERS = (
    ("Apache-2.0", "apache license", "version 2.0"),
    ("MPL-2.0", "mozilla public license", "2.0"),
    ("MIT", "permission is hereby granted, free of charge", ""),
    ("BSD-3-Clause", "redistribution and use in source and binary forms", "neither the name"),
)


def _text(expression: str | None) -> str | None:
    """Plain text of a description expression; None when it is not a simple literal."""
    if expression is None:
        return None
    heredoc = HEREDOC.match(expression.strip())
    if heredoc:
        lines = heredoc.group(2).splitlines()
        indent = min((len(line) - len(line.lstrip()) for line in lines if line.strip()), default=0)
        return "\n".join(line[indent:] for line in lines).strip()
    return literal_string(expression)


def extract_interface(
    entities: list[TerraformEntity],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """Variables and outputs declared by the root module, sorted by name.

    type and default stay as HCL source text: nothing is evaluated.
    """
    variables, outputs = [], []
    for entity in entities:
        if entity.metadata.get("module_path"):
            continue  # Declared by a child module, not part of this module's interface.
        attributes = entity.attributes
        common = {
            "name": entity.name,
            "description": _text(attributes.get("description")),
            "sensitive": attributes.get("sensitive") == "true",
        }
        if entity.entity_type == EntityType.VARIABLE:
            variables.append(
                {
                    **common,
                    "type": attributes.get("type"),
                    "default": attributes.get("default"),
                    "required": "default" not in attributes,
                }
            )
        elif entity.entity_type == EntityType.OUTPUT:
            outputs.append(common)
    return sorted(variables, key=lambda v: v["name"]), sorted(outputs, key=lambda o: o["name"])


# Helper providers that say nothing about where a module deploys.
UTILITY_PROVIDERS = {
    "archive",
    "cloudinit",
    "external",
    "http",
    "local",
    "null",
    "random",
    "template",
    "time",
    "tls",
}


def extract_provider(entities: list[TerraformEntity]) -> str | None:
    """Main provider from the root module's required_providers (aws, google, azurerm, …)."""
    names = sorted(
        entity.name.lower()
        for entity in entities
        if entity.entity_type == EntityType.REQUIRED_PROVIDER
        and not entity.metadata.get("module_path")
    )
    primary = [name for name in names if name not in UTILITY_PROVIDERS]
    chosen = (primary or names or [None])[0]
    return chosen[:100] if chosen else None


def _root_file(root: Path, matches) -> Path | None:
    candidates = sorted(
        entry
        for entry in root.iterdir()
        if entry.is_file() and not entry.is_symlink() and matches(entry.name.lower())
    )
    return candidates[0] if candidates else None


def read_readme(root: Path) -> str | None:
    path = _root_file(root, lambda name: name == "readme.md")
    if path is None:
        return None
    with path.open("rb") as handle:
        return handle.read(README_LIMIT_BYTES).decode("utf-8", errors="replace")


def detect_license(root: Path) -> str | None:
    """SPDX id for a few common licenses; None when absent or not recognised."""
    path = _root_file(root, lambda name: name.split(".")[0] in {"license", "licence", "copying"})
    if path is None:
        return None
    with path.open("rb") as handle:
        head = " ".join(handle.read(4096).decode("utf-8", errors="replace").lower().split())
    for spdx, first, second in LICENSE_MARKERS:
        if first in head and second in head:
            return spdx
    return None
