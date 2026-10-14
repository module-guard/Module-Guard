import json
from copy import deepcopy
from pathlib import Path
from uuid import uuid4

import hcl2
from lark import Tree

from app.parsers.base import TerraformParser
from app.parsers.domain import (
    EntityType,
    ParseDiagnostic,
    TerraformEntity,
    TerraformParseResult,
    qualify,
)
from app.utils.filesystem import EXCLUDED_DIRECTORIES


def children(tree: Tree, name: str) -> list[Tree]:
    return [child for child in tree.children if isinstance(child, Tree) and child.data == name]


def source_text(tree: Tree, text: str) -> str:
    return text[tree.meta.start_pos : tree.meta.end_pos]


def literal_string(expression: str | None) -> str | None:
    if expression is None or "${" in expression or "%{" in expression:
        return None
    try:
        value = json.loads(expression)
        return value if isinstance(value, str) else None
    except ValueError:
        return None


class HCLTerraformParser(TerraformParser):
    """One syntax parse per real directory/file; instantiate declarations per module call.

    File offsets come from python-hcl2's Lark syntax tree. No Terraform evaluation runs.
    Mutable traversal state is local to parse_directory, so the parser is worker-safe.
    """

    def __init__(
        self, max_depth: int = 32, max_entities: int = 20000, max_file_bytes: int = 2 * 1024**2
    ):
        self.max_depth = max_depth
        self.max_entities = max_entities
        self.max_file_bytes = max_file_bytes

    def parse_directory(self, root: Path) -> TerraformParseResult:
        root = root.resolve(strict=True)
        result = TerraformParseResult()
        cache: dict[Path, list[TerraformEntity]] = {}
        addresses: set[str] = set()

        def diagnose(code: str, path: Path, message: str, scope: list[str]) -> None:
            relative = (
                str(path.relative_to(root)) if path.is_relative_to(root) else "<outside-repository>"
            )
            result.diagnostics.append(
                ParseDiagnostic(code=code, file_path=relative, message=message, module_path=scope)
            )

        def load(directory: Path) -> list[TerraformEntity]:
            if directory in cache:
                return cache[directory]
            declarations = []
            candidate_count = 0
            result.directories_parsed += 1
            for path in sorted(directory.iterdir()):
                if path.is_symlink():
                    diagnose("UNSAFE_PATH", path, "Symbolic link skipped.", [])
                    continue
                if path.name.endswith(".tf.json"):
                    diagnose(
                        "UNSUPPORTED_JSON",
                        path,
                        "Graph parsing currently supports .tf files only.",
                        [],
                    )
                if not path.is_file() or path.suffix != ".tf":
                    continue
                candidate_count += 1
                try:
                    if path.stat().st_size > self.max_file_bytes:
                        raise ValueError("file size limit")
                    text = path.read_text(encoding="utf-8")
                    tree = hcl2.parses_to_tree(text)
                    declarations.extend(self._entities(tree, text, str(path.relative_to(root))))
                    result.files_parsed += 1
                except Exception:
                    result.files_failed += 1
                    diagnose(
                        "FILE_PARSE_FAILED",
                        path,
                        "File could not be parsed within configured limits.",
                        [],
                    )
            if candidate_count == 0:
                diagnose("NO_HCL_FILES", directory, "Module directory contains no .tf files.", [])
            cache[directory] = declarations
            return declarations

        def visit(
            directory: Path, scope: list[str], ancestors: frozenset[Path], owner: TerraformEntity
        ) -> None:
            for prototype in load(directory):
                if prototype.entity_type == EntityType.ROOT_MODULE:
                    owner.metadata.setdefault("terraform_configurations", []).append(
                        {
                            "file_path": prototype.file_path,
                            "line_start": prototype.line_start,
                            "line_end": prototype.line_end,
                            "attributes": prototype.attributes,
                        }
                    )
                    continue
                if len(result.entities) >= self.max_entities:
                    diagnose("ENTITY_LIMIT", directory, "Entity expansion limit reached.", scope)
                    return
                entity = prototype.model_copy(deep=True, update={"id": uuid4()})
                entity.address = qualify(prototype.address, scope)
                entity.metadata["module_path"] = scope.copy()
                if len(entity.address.encode("utf-8")) > 2048:
                    diagnose(
                        "ADDRESS_LIMIT",
                        root / entity.file_path,
                        "Entity address exceeds the storage limit.",
                        scope,
                    )
                    continue
                if entity.address in addresses:
                    diagnose(
                        "DUPLICATE_ADDRESS",
                        root / entity.file_path,
                        "Duplicate declaration address omitted.",
                        scope,
                    )
                    continue
                addresses.add(entity.address)
                result.entities.append(entity)
                if entity.entity_type != EntityType.MODULE:
                    continue
                source = entity.metadata.get("source")
                entity.metadata["expansion"] = "external" if source else "unknown"
                if not source or not source.startswith(("./", "../")):
                    continue
                child = directory / source
                resolved = child.resolve()
                if not resolved.is_relative_to(root) or any(
                    p.is_symlink() for p in [child, *child.parents] if p.is_relative_to(root)
                ):
                    entity.metadata["expansion"] = "unsafe"
                    diagnose(
                        "UNSAFE_MODULE_PATH",
                        directory,
                        "Local module path escapes the repository or uses a symlink.",
                        scope,
                    )
                elif any(part in EXCLUDED_DIRECTORIES for part in resolved.relative_to(root).parts):
                    entity.metadata["expansion"] = "excluded"
                    diagnose(
                        "EXCLUDED_MODULE_PATH",
                        resolved,
                        "Local module is in an excluded directory.",
                        scope,
                    )
                elif resolved in ancestors:
                    entity.metadata["expansion"] = "cycle"
                    diagnose(
                        "MODULE_CYCLE", resolved, "Recursive local module cycle stopped.", scope
                    )
                elif len(scope) >= self.max_depth:
                    entity.metadata["expansion"] = "depth_limit"
                    diagnose("DEPTH_LIMIT", resolved, "Local module depth limit reached.", scope)
                elif not resolved.is_dir():
                    entity.metadata["expansion"] = "missing"
                    diagnose(
                        "MISSING_LOCAL_MODULE",
                        resolved,
                        "Local module directory is unavailable.",
                        scope,
                    )
                else:
                    entity.metadata["expansion"] = "expanded"
                    visit(resolved, [*scope, entity.name], ancestors | {resolved}, entity)

        result.entities.append(
            TerraformEntity(
                entity_type=EntityType.ROOT_MODULE,
                address="root",
                name="root",
                file_path=".",
                metadata={"module_path": [], "graph_schema_version": 1},
            )
        )
        addresses.add("root")
        visit(root, [], frozenset({root}), result.entities[0])
        return result

    def _attributes(
        self,
        body: Tree,
        text: str,
        prefix: str = "",
        *,
        scopes: dict[str, list[str]] | None = None,
        bound: list[str] | None = None,
    ) -> dict[str, str]:
        bound = bound or []
        attributes = {}
        for attr in children(body, "attribute"):
            name = source_text(next(c for c in attr.children if isinstance(c, Tree)), text)
            expr = next(
                c for c in reversed(attr.children) if isinstance(c, Tree) and c.data != "identifier"
            )
            attributes[prefix + name] = source_text(expr, text)
            if scopes is not None and bound:
                scopes[prefix + name] = bound.copy()
        counts: dict[str, int] = {}
        for block in children(body, "block"):
            kind = source_text(children(block, "identifier")[0], text)
            index = counts.get(kind, 0)
            counts[kind] = index + 1
            block_body = children(block, "body")[0]
            nested_bound = bound
            if kind == "dynamic":
                names = children(block, "string")
                iterator = literal_string(source_text(names[0], text)) if names else None
                for attribute in children(block_body, "attribute"):
                    if (
                        source_text(
                            next(c for c in attribute.children if isinstance(c, Tree)), text
                        )
                        == "iterator"
                    ):
                        iterator = source_text(attribute.children[-1], text)
                # Iterator is not in scope in for_each. Apply it only to nested content blocks.
                dynamic_prefix = f"{prefix}{kind}[{index}]."
                direct = self._attributes(
                    block_body, text, dynamic_prefix, scopes=scopes, bound=bound
                )
                attributes.update(direct)
                if iterator and scopes is not None:
                    for key in direct:
                        if key.startswith(dynamic_prefix + "content["):
                            scopes[key] = [*scopes.get(key, bound), iterator]
                continue
            attributes.update(
                self._attributes(
                    block_body, text, f"{prefix}{kind}[{index}].", scopes=scopes, bound=nested_bound
                )
            )
        return attributes

    def _entities(self, tree: Tree, text: str, file_path: str) -> list[TerraformEntity]:
        entities = []
        for block in children(children(tree, "body")[0], "block"):
            kind = source_text(children(block, "identifier")[0], text)
            # Preserve label order even when quoted and unquoted labels are mixed.
            labels = [
                literal_string(source_text(label, text))
                if label.data == "string"
                else source_text(label, text)
                for label in [
                    c
                    for c in block.children
                    if isinstance(c, Tree) and c.data in {"identifier", "string"}
                ][1:]
            ]
            body = children(block, "body")[0]
            attribute_scopes: dict[str, list[str]] = {}
            attributes = self._attributes(body, text, scopes=attribute_scopes)
            metadata = {"module_path": [], "attribute_scopes": attribute_scopes}
            if kind == "terraform":
                for required in children(body, "block"):
                    if (
                        source_text(children(required, "identifier")[0], text)
                        != "required_providers"
                    ):
                        continue
                    for attr in children(children(required, "body")[0], "attribute"):
                        name = source_text(
                            next(c for c in attr.children if isinstance(c, Tree)), text
                        )
                        expr = next(
                            c
                            for c in reversed(attr.children)
                            if isinstance(c, Tree) and c.data != "identifier"
                        )
                        values = {}
                        # The object keys describe source/version/configuration_aliases, not entities.
                        objects = children(expr, "object")
                        if objects:
                            for item in children(objects[0], "object_elem"):
                                key = source_text(children(item, "object_elem_key")[0], text).strip(
                                    '"'
                                )
                                value = next(
                                    c
                                    for c in reversed(item.children)
                                    if isinstance(c, Tree) and c.data != "object_elem_key"
                                )
                                values[key] = source_text(value, text)
                        else:
                            values["version"] = source_text(
                                expr, text
                            )  # legacy provider version syntax
                        entities.append(
                            self._entity(
                                EntityType.REQUIRED_PROVIDER,
                                f"required_provider.{name}",
                                name,
                                file_path,
                                attr,
                                values,
                                {
                                    "source": literal_string(values.get("source")),
                                    "version": literal_string(values.get("version")),
                                    "module_path": [],
                                },
                            )
                        )
                entities.append(
                    self._entity(
                        EntityType.ROOT_MODULE,
                        "root",
                        "root",
                        file_path,
                        block,
                        attributes,
                        metadata,
                    )
                )
                continue
            if kind == "locals":
                for attr in children(body, "attribute"):
                    name = source_text(next(c for c in attr.children if isinstance(c, Tree)), text)
                    entities.append(
                        self._entity(
                            EntityType.LOCAL,
                            f"local.{name}",
                            name,
                            file_path,
                            attr,
                            {"value": attributes[name]},
                            metadata.copy(),
                        )
                    )
                continue
            if kind in ("resource", "data") and len(labels) == 2:
                resource_type, name = labels
                address = (
                    f"{resource_type}.{name}"
                    if kind == "resource"
                    else f"data.{resource_type}.{name}"
                )
                entities.append(
                    self._entity(
                        EntityType.RESOURCE if kind == "resource" else EntityType.DATA,
                        address,
                        name,
                        file_path,
                        block,
                        attributes,
                        metadata,
                        resource_type,
                    )
                )
            elif kind in ("module", "variable", "output", "provider") and len(labels) == 1:
                name = labels[0]
                prefix = {
                    "module": "module",
                    "variable": "var",
                    "output": "output",
                    "provider": "provider",
                }[kind]
                address = f"{prefix}.{name}"
                if kind == "module":
                    metadata.update(
                        source=literal_string(attributes.get("source")),
                        version=literal_string(attributes.get("version")),
                    )
                if kind == "provider":
                    alias = literal_string(attributes.get("alias"))
                    metadata["alias"] = alias
                    if alias:
                        address += f".{alias}"
                entities.append(
                    self._entity(
                        EntityType(kind.upper()),
                        address,
                        name,
                        file_path,
                        block,
                        attributes,
                        metadata,
                    )
                )
        return entities

    @staticmethod
    def _entity(
        kind: EntityType,
        address: str,
        name: str,
        file: str,
        node: Tree,
        attributes: dict[str, str],
        metadata: dict,
        resource_type: str | None = None,
    ) -> TerraformEntity:
        return TerraformEntity(
            entity_type=kind,
            address=address,
            name=name,
            resource_type=resource_type,
            file_path=file,
            line_start=getattr(node.meta, "line", None),
            line_end=getattr(node.meta, "end_line", None),
            attributes=attributes,
            metadata=deepcopy(metadata),
        )
