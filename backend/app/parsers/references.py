from abc import ABC, abstractmethod

import hcl2
from lark import Tree

from app.graphs.domain import EdgeType, Reference
from app.parsers.domain import qualify
from app.parsers.hcl import children, source_text


class ReferenceExtractor(ABC):
    @abstractmethod
    def extract(
        self,
        source_address: str,
        expression: str,
        attribute_path: str,
        module_path: list[str],
        *,
        provider: bool = False,
        bound_names: frozenset[str] = frozenset(),
    ) -> list[Reference]: ...


class HCLReferenceExtractor(ReferenceExtractor):
    """Syntax traversal only: distinguish literal text, interpolations, indexes and loop scopes."""

    _chains = {
        "get_attr_expr_term",
        "index_expr_term",
        "full_splat_expr_term",
        "attr_splat_expr_term",
    }
    _builtins = {"each", "count", "path", "terraform", "self"}

    def extract(
        self,
        source_address: str,
        expression: str,
        attribute_path: str,
        module_path: list[str],
        *,
        provider: bool = False,
        bound_names: frozenset[str] = frozenset(),
    ) -> list[Reference]:
        text = "value = " + expression
        tree = hcl2.parses_to_tree(text)
        references: list[Reference] = []
        seen: set[tuple[str, str, str]] = set()

        def chain(node: Tree) -> tuple[list[str], list[Tree]] | None:
            if (
                node.data == "expr_term"
                and len(node.children) == 1
                and isinstance(node.children[0], Tree)
            ):
                return chain(node.children[0])
            if node.data == "identifier":
                return [source_text(node, text)], []
            if node.data not in self._chains:
                return None
            trees = [c for c in node.children if isinstance(c, Tree)]
            head = chain(trees[0])
            if head is None:
                return None
            parts, extras = head
            for suffix in trees[1:]:
                if suffix.data == "get_attr":
                    parts.append(source_text(children(suffix, "identifier")[0], text))
                elif suffix.data in ("full_splat", "attr_splat"):
                    for attr in children(suffix, "get_attr"):
                        parts.append(source_text(children(attr, "identifier")[0], text))
                    extras.extend(children(suffix, "braces_index"))
                else:
                    extras.append(suffix)
            return parts, extras

        def emit(parts: list[str], node: Tree, bound: frozenset[str]) -> None:
            if not parts or parts[0] in bound or parts[0] in self._builtins:
                return
            root = parts[0]
            target_attr = None
            if provider:
                if root in {"true", "false", "null"}:
                    return
                address = "provider." + ".".join(parts[:2])
                kind = EdgeType.USES_PROVIDER
            elif root in {"var", "local"} and len(parts) >= 2:
                address = ".".join(parts[:2])
                kind = EdgeType.READS_VARIABLE if root == "var" else EdgeType.READS_LOCAL
                target_attr = ".".join(parts[2:]) or None
            elif root == "data" and len(parts) >= 3:
                address = ".".join(parts[:3])
                kind = EdgeType.READS_DATA
                target_attr = ".".join(parts[3:]) or None
            elif root == "module" and len(parts) >= 2:
                address = ".".join(parts[:2])
                target_attr = ".".join(parts[2:]) or None
                kind = EdgeType.CONSUMES_MODULE_OUTPUT if target_attr else EdgeType.REFERENCES
            elif len(parts) >= 2 and root not in {"var", "local", "module", "data"}:
                address = ".".join(parts[:2])
                kind = EdgeType.RESOURCE_DEPENDS_ON
                target_attr = ".".join(parts[2:]) or None
            else:
                return
            if attribute_path == "depends_on":
                kind = EdgeType.EXPLICIT_DEPENDS_ON
            address = qualify(address, module_path)
            traversal = source_text(node, text)
            key = (address, traversal, kind)
            if key not in seen:
                seen.add(key)
                references.append(
                    Reference(
                        source_address=source_address,
                        target_address=address,
                        reference_type=kind,
                        expression=expression,
                        attribute_path=attribute_path,
                        traversal=traversal,
                        target_attribute=target_attr,
                    )
                )

        def walk(node: Tree, bound: frozenset[str]) -> None:
            if node.data == "object_elem_key":
                # Bare object keys are names. Parenthesized keys may contain references.
                if not source_text(node, text).lstrip().startswith("("):
                    return
            if node.data in {"for_tuple_expr", "for_object_expr"}:
                intro = children(node, "for_intro")[0]
                for expr in children(intro, "expr_term"):
                    walk(expr, bound)
                loop_names = frozenset(source_text(n, text) for n in children(intro, "identifier"))
                for child in node.children:
                    if isinstance(child, Tree) and child is not intro:
                        walk(child, bound | loop_names)
                return
            if node.data == "string":
                scopes = [bound]
                for part in children(node, "string_part"):
                    starts = children(part, "template_for_start")
                    if starts:
                        intro = starts[0]
                        for expr in children(intro, "expr_term"):
                            walk(expr, scopes[-1])
                        names = frozenset(
                            source_text(n, text) for n in children(intro, "identifier")
                        )
                        scopes.append(scopes[-1] | names)
                    elif children(part, "template_endfor"):
                        if len(scopes) > 1:
                            scopes.pop()
                    else:
                        for child in part.children:
                            if isinstance(child, Tree):
                                walk(child, scopes[-1])
                return
            if node.data in self._chains:
                traversal = chain(node)
                if traversal is not None:
                    parts, extras = traversal
                    emit(parts, node, bound)
                    for extra in extras:
                        walk(extra, bound)
                    return
            if provider and node.data == "identifier":
                emit([source_text(node, text)], node, bound)
                return
            for child in node.children:
                if isinstance(child, Tree):
                    walk(child, bound)

        # Start at the value, excluding the synthetic attribute identifier.
        attr = children(children(tree, "body")[0], "attribute")[0]
        expression_node = next(
            c for c in reversed(attr.children) if isinstance(c, Tree) and c.data != "identifier"
        )
        walk(expression_node, bound_names)
        return references
