"""Reverse value-bearing references, never dependency ordering edges or runtime evaluation."""

import re

import hcl2

from app.analyzers.flow_classifiers import FlowSinkClassifier, FlowSourceClassifier
from app.graphs.domain import DependencyEdge, DependencyGraph, EdgeType
from app.graphs.flow_domain import DataFlowResult, summarize_flows
from app.parsers.domain import EntityType, TerraformEntity, scope_address
from app.parsers.hcl import children, source_text


class DataFlowGraphBuilder:
    def __init__(self, max_edges: int = 100000):
        self.max_edges = max_edges
        self.sources = FlowSourceClassifier()
        self.sinks = FlowSinkClassifier()

    def build(self, graph: DependencyGraph) -> DataFlowResult:
        by_address = {e.address: e for e in graph.nodes}
        by_id = {e.id: e for e in graph.nodes}
        # These indexes preserve separate instances of the same physical local directory.
        variables = {
            (tuple(e.metadata.get("module_path", [])), e.name): e
            for e in graph.nodes
            if e.entity_type == EntityType.VARIABLE
        }
        outputs = {
            (tuple(e.metadata.get("module_path", [])), e.name): e
            for e in graph.nodes
            if e.entity_type == EntityType.OUTPUT
        }
        references: dict = {}
        # Use the complete extraction stream: dependency-edge dedup can collapse two
        # different outputs of one module in a single conditional expression.
        value_types = {
            EdgeType.READS_VARIABLE,
            EdgeType.READS_LOCAL,
            EdgeType.READS_DATA,
            EdgeType.CONSUMES_MODULE_OUTPUT,
            EdgeType.RESOURCE_DEPENDS_ON,
        }
        if graph.references:
            for ref in graph.references:
                if ref.reference_type not in value_types:
                    continue
                consumer = by_address[ref.source_address]
                references.setdefault(consumer.id, []).append(
                    DependencyEdge(
                        source_entity_id=consumer.id,
                        target_address=ref.target_address,
                        edge_type=ref.reference_type,
                        expression=ref.expression,
                        attribute_name=ref.attribute_path,
                        metadata={
                            "target_attribute": ref.target_attribute,
                            "traversal": ref.traversal,
                        },
                    )
                )
        else:
            for edge in graph.edges:
                if edge.edge_type in value_types:
                    references.setdefault(edge.source_entity_id, []).append(edge)
        result = DataFlowResult()
        seen = set()
        facts_cache: dict[str, dict] = {}
        limit_reported = False

        def endpoint(
            entity: TerraformEntity | None,
            address: str,
            attribute: str | None,
            *,
            external: bool = False,
            reason: str | None = None,
            boundary: TerraformEntity | None = None,
            module_path: list[str] | None = None,
        ) -> dict:
            boundary = boundary or (
                entity if entity and entity.entity_type == EntityType.MODULE else None
            )
            return {
                "address": address,
                "entity_id": str(entity.id) if entity else None,
                "entity_address": entity.address if entity else None,
                "entity_type": str(entity.entity_type) if entity else "UNRESOLVED",
                "attribute_path": attribute,
                "resolved": entity is not None and reason is None,
                "external": external,
                "opaque": external,
                "reason": reason,
                "module_path": module_path
                if module_path is not None
                else (entity.metadata.get("module_path", []) if entity else []),
                "boundary_entity_id": str(boundary.id) if boundary else None,
                "boundary_address": boundary.address if boundary else None,
                "module_source": boundary.metadata.get("source") if boundary else None,
                "module_source_type": boundary.metadata.get("source_type") if boundary else None,
                "module_expansion": boundary.metadata.get("expansion") if boundary else None,
                "file_path": entity.file_path if entity else None,
                "line_start": entity.line_start if entity else None,
                "line_end": entity.line_end if entity else None,
                "source_category": str(self.sources.classify(entity)),
                "sink_category": self.sinks.classify(entity, attribute),
            }

        def emit(
            source: dict,
            target: dict,
            kind: str,
            expression: str | None,
            attribute: str | None,
            extra: dict | None = None,
        ) -> None:
            nonlocal limit_reported
            key = (source["address"], target["address"], kind, expression, attribute)
            if key in seen:
                return
            if len(result.edges) >= self.max_edges:
                if not limit_reported:
                    result.diagnostics.append(
                        {
                            "code": "FLOW_EDGE_LIMIT",
                            "level": "warning",
                            "message": "Data flow edge limit reached.",
                        }
                    )
                    limit_reported = True
                return
            seen.add(key)
            result.edges.append(
                DependencyEdge(
                    source_entity_id=source["entity_id"],
                    target_entity_id=target["entity_id"],
                    target_address=target["address"],
                    edge_type=kind,
                    expression=expression,
                    attribute_name=attribute,
                    metadata={
                        "graph_kind": "data_flow",
                        "flow_schema_version": 1,
                        "source": source,
                        "target": target,
                        "source_address": source["address"],
                        "target_address": target["address"],
                        "resolved": source["resolved"] and target["resolved"],
                        "opaque": source["opaque"] or target["opaque"],
                        "external": source["external"] or target["external"],
                        "cross_module": source["module_path"] != target["module_path"]
                        or kind in {"PASSES_TO_MODULE_INPUT", "RETURNS_FROM_MODULE_OUTPUT"},
                        "source_category": source["source_category"],
                        "sink_category": target["sink_category"],
                        **(extra or {}),
                    },
                )
            )

        # Output declaration -> public module output alias, even when its value is literal.
        for (scope, name), output in outputs.items():
            if not scope:
                continue
            module = by_address.get(scope_address(list(scope)))
            if module:
                emit(
                    endpoint(output, output.address, None),
                    endpoint(module, module.address + "." + name, name),
                    "RETURNS_FROM_MODULE_OUTPUT",
                    None,
                    None,
                )

        for consumer_id, refs in references.items():
            consumer = by_id[consumer_id]
            for reference in refs:
                attribute = reference.attribute_name or ""
                kind = consumer.entity_type
                target_entity = consumer
                target_attribute: str | None = attribute
                target_reason = None
                external_target = False
                # Configuration ordering, provider binding and meta arguments are not value sinks.
                if attribute.split(".", 1)[0].split("[", 1)[0] in {
                    "depends_on",
                    "provider",
                    "providers",
                    "count",
                    "for_each",
                    "lifecycle",
                }:
                    continue
                if kind in {EntityType.LOCAL, EntityType.OUTPUT} and attribute == "value":
                    target_address = consumer.address
                    target_attribute = None
                    edge_type = (
                        "PROPAGATES_THROUGH_LOCAL"
                        if kind == EntityType.LOCAL
                        else "PASSES_TO_OUTPUT"
                    )
                elif kind in {EntityType.RESOURCE, EntityType.DATA}:
                    if attribute.startswith("dynamic["):
                        # Retain other supported expressions, but do not invent a dynamic instance.
                        continue
                    target_attribute = re.sub(r"\[\d+\]", "", attribute)
                    target_address = consumer.address + "." + target_attribute
                    edge_type = (
                        "SINKS_TO_RESOURCE_ATTRIBUTE" if kind == EntityType.RESOURCE else "FLOWS_TO"
                    )
                elif kind == EntityType.MODULE and attribute not in {"source", "version"}:
                    scope = (*consumer.metadata.get("module_path", []), consumer.name)
                    target_entity = variables.get((scope, attribute))
                    external_target = consumer.metadata.get("source_type") in {
                        "REGISTRY",
                        "GIT",
                        "GITHUB",
                        "HTTP",
                    }
                    target_address = consumer.address + ".var." + attribute
                    if target_entity is None:
                        target_reason = (
                            "external_module" if external_target else "unresolved_module_input"
                        )
                    target_attribute = None
                    edge_type = "PASSES_TO_MODULE_INPUT"
                else:
                    continue
                source_entity = by_address.get(reference.target_address)
                source_attribute = reference.metadata.get("target_attribute")
                source_address = reference.target_address
                reason = "undeclared_reference" if source_entity is None else None
                external = False
                if source_entity and source_entity.entity_type == EntityType.MODULE:
                    # Collections of outputs are conservatively represented at output-name granularity.
                    source_attribute = (source_attribute or "").split(".")[0] or None
                    external = source_entity.metadata.get("source_type") in {
                        "REGISTRY",
                        "GITHUB",
                        "GIT",
                        "HTTP",
                    }
                    child_scope = (
                        *source_entity.metadata.get("module_path", []),
                        source_entity.name,
                    )
                    if external:
                        reason = "external_module"
                    elif (child_scope, source_attribute) not in outputs:
                        reason = "unresolved_module_output"
                elif source_entity and source_entity.entity_type in {
                    EntityType.LOCAL,
                    EntityType.VARIABLE,
                }:
                    # Object fields aggregate at value nodes, avoiding false precision and broken paths.
                    source_attribute = None
                if source_attribute:
                    source_address += "." + source_attribute
                expression = reference.expression or ""
                if expression not in facts_cache:
                    facts_cache[expression] = self.expression_facts(expression)
                emit(
                    endpoint(
                        source_entity,
                        source_address,
                        source_attribute,
                        external=external,
                        reason=reason,
                        module_path=consumer.metadata.get("module_path", [])
                        if source_entity is None
                        else None,
                    ),
                    endpoint(
                        target_entity,
                        target_address,
                        target_attribute,
                        external=external_target,
                        reason=target_reason,
                        boundary=consumer if kind == EntityType.MODULE else None,
                        module_path=list(scope) if kind == EntityType.MODULE else None,
                    ),
                    edge_type,
                    reference.expression,
                    attribute,
                    {
                        **facts_cache[expression],
                        "traversal": reference.metadata.get("traversal"),
                        "referenced_attribute": reference.metadata.get("target_attribute"),
                        "potential": True,
                        "object_precision": "aggregate",
                    },
                )
        for entity in graph.nodes:
            if entity.metadata.get("attribute_scopes"):
                result.diagnostics.append(
                    {
                        "code": "DYNAMIC_ITERATOR_FLOW_UNRESOLVED",
                        "level": "warning",
                        "file_path": entity.file_path,
                        "line": entity.line_start,
                        "message": "Dynamic iterator instance flows are not expanded.",
                    }
                )
        if any(f.get("collection_iteration") for f in facts_cache.values()):
            result.diagnostics.append(
                {
                    "code": "COLLECTION_FLOW_AGGREGATED",
                    "level": "warning",
                    "message": "Loop flows use collection references; iterator field projection is not evaluated.",
                }
            )
        result.summary = summarize_flows(result.edges)
        return result

    @staticmethod
    def expression_facts(expression: str) -> dict:
        try:
            text = "value = " + expression
            tree = hcl2.parses_to_tree(text)
            functions = set()
            for node in tree.find_data("function_call"):
                identifiers = children(node, "identifier")
                if identifiers:
                    functions.add(source_text(identifiers[0], text))
            return {
                "transformations": sorted(functions),
                "conditional": any(tree.find_data("conditional")),
                "collection_iteration": any(tree.find_data("for_tuple_expr"))
                or any(tree.find_data("for_object_expr")),
            }
        except Exception:
            return {"expression_facts_unavailable": True}
