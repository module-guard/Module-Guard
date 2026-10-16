"""Terraform JSON declarations, normalized to the existing static expression model."""

import json
from collections.abc import Iterator
from typing import Any

from app.parsers.domain import EntityType, TerraformEntity

LABELS = {
    "resource": 2,
    "data": 2,
    "module": 1,
    "variable": 1,
    "output": 1,
    "locals": 0,
    "provider": 1,
    "terraform": 0,
}


def _object(value: Any) -> dict:
    if not isinstance(value, dict):
        raise ValueError("Expected a JSON block object.")
    return value


def _blocks(value: Any, labels: int) -> Iterator[tuple[list[str], dict]]:
    # Arrays preserve repeated block order at any label level, including provider aliases.
    for block in value if isinstance(value, list) else [value]:
        body = _object(block)
        if labels == 0:
            yield [], body
        else:
            for name, nested in body.items():
                for rest, attributes in _blocks(nested, labels - 1):
                    yield [name, *rest], attributes


def _unique_object(pairs: list[tuple[str, Any]]) -> dict:
    result = {}
    for name, value in pairs:
        if name in result:
            raise ValueError("Duplicate JSON object property.")
        result[name] = value
    return result


def _reject_constant(value: str) -> None:
    raise ValueError("Non-JSON numeric constant.")


def _expression(value: Any, *, literal: bool = False) -> str:
    if literal:
        if isinstance(value, str):
            # JSON defaults/descriptions/source settings are literal, not string templates.
            value = value.replace("${", "$${").replace("%{", "%%{")
        elif isinstance(value, list):
            return "[" + ", ".join(_expression(item, literal=True) for item in value) + "]"
        elif isinstance(value, dict):
            return (
                "{"
                + ", ".join(
                    json.dumps(key) + ": " + _expression(item, literal=True)
                    for key, item in value.items()
                )
                + "}"
            )
    return json.dumps(value, ensure_ascii=False, allow_nan=False)


def _reference(value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError("Expected a literal reference string.")
    return value


def _attributes(kind: str, body: dict) -> dict[str, str]:
    attributes = {}
    for name, value in body.items():
        if name == "//" or (name == "dynamic" and kind in {"resource", "data", "provider"}):
            continue
        if name == "depends_on" and kind in {"resource", "data", "module", "output"}:
            if not isinstance(value, list):
                raise ValueError("Expected a depends_on array.")
            expression = "[" + ", ".join(_reference(item) for item in value) + "]"
        elif name == "provider" and kind in {"resource", "data"}:
            expression = _reference(value)
        elif name == "providers" and kind == "module":
            expression = (
                "{"
                + ", ".join(
                    json.dumps(key) + ": " + _reference(item)
                    for key, item in _object(value).items()
                )
                + "}"
            )
        elif name == "type" and kind == "variable":
            expression = _reference(value)
        else:
            literal = (
                kind == "terraform"
                or (kind == "variable" and name != "validation")
                or (kind == "output" and name in {"description", "sensitive", "ephemeral"})
                or (kind == "module" and name in {"source", "version"})
                or (kind == "provider" and name in {"alias", "version"})
            )
            expression = _expression(value, literal=literal)
        attributes[name] = expression
    return attributes


def _literal(value: Any) -> str | None:
    return value if isinstance(value, str) and not any(x in value for x in ("${", "%{")) else None


def parse_json_entities(
    text: str, file_path: str
) -> tuple[list[TerraformEntity], list[tuple[str, str]]]:
    document = _object(
        json.loads(text, object_pairs_hook=_unique_object, parse_constant=_reject_constant)
    )
    entities = []
    diagnostics = []

    def entity(kind, address, name, attributes, metadata=None, resource_type=None):
        entities.append(
            TerraformEntity(
                entity_type=kind,
                address=address,
                name=name,
                resource_type=resource_type,
                file_path=file_path,
                # JSON decoding does not provide source ranges. Never invent line numbers.
                attributes=attributes,
                metadata={"module_path": [], **(metadata or {})},
            )
        )

    for kind, value in document.items():
        if kind == "//":
            continue
        if kind not in LABELS:
            diagnostics.append(
                ("UNSUPPORTED_JSON_BLOCK", f"Unsupported Terraform JSON block: {kind}.")
            )
            continue
        for labels, body in _blocks(value, LABELS[kind]):
            attributes = _attributes(kind, body)
            if "dynamic" in body and kind in {"resource", "data", "provider"}:
                diagnostics.append(
                    ("UNSUPPORTED_JSON_DYNAMIC", "JSON dynamic blocks are not expanded.")
                )
            if kind == "terraform":
                for _, requirements in _blocks(body.get("required_providers", {}), 0):
                    for name, requirement in requirements.items():
                        if name == "//":
                            continue
                        values = (
                            {"version": requirement}
                            if isinstance(requirement, str)
                            else _object(requirement)
                        )
                        entity(
                            EntityType.REQUIRED_PROVIDER,
                            f"required_provider.{name}",
                            name,
                            {
                                key: _expression(item, literal=True)
                                for key, item in values.items()
                                if key != "//"
                            },
                            {
                                "source": _literal(values.get("source")),
                                "version": _literal(values.get("version")),
                            },
                        )
                entity(EntityType.ROOT_MODULE, "root", "root", attributes)
            elif kind == "locals":
                for name, expression in attributes.items():
                    entity(EntityType.LOCAL, f"local.{name}", name, {"value": expression})
            elif kind in {"resource", "data"}:
                resource_type, name = labels
                address = f"{resource_type}.{name}"
                entity(
                    EntityType.RESOURCE if kind == "resource" else EntityType.DATA,
                    address if kind == "resource" else "data." + address,
                    name,
                    attributes,
                    resource_type=resource_type,
                )
            else:
                name = labels[0]
                address = ("var" if kind == "variable" else kind) + "." + name
                metadata = {}
                if kind == "module":
                    metadata = {
                        "source": _literal(body.get("source")),
                        "version": _literal(body.get("version")),
                    }
                elif kind == "provider":
                    alias = _literal(body.get("alias"))
                    metadata["alias"] = alias
                    if alias:
                        address += "." + alias
                entity(EntityType(kind.upper()), address, name, attributes, metadata)
    return entities, diagnostics
