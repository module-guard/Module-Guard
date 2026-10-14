from app.analyzers.classifiers import SourceClassifier, VersionConstraintClassifier
from app.graphs.domain import DependencyEdge, DependencyGraph, EdgeType
from app.parsers.domain import EntityType, TerraformParseResult, qualify, scope_address
from app.parsers.hcl import literal_string
from app.parsers.references import HCLReferenceExtractor, ReferenceExtractor


class DependencyGraphBuilder:
    def __init__(self, extractor: ReferenceExtractor | None = None, max_edges: int = 100000):
        self.max_edges = max_edges
        self.extractor = extractor or HCLReferenceExtractor()

    def build(self, parsed: TerraformParseResult) -> DependencyGraph:
        nodes = [entity.model_copy(deep=True) for entity in parsed.entities]
        by_address = {entity.address: entity for entity in nodes}
        graph = DependencyGraph(nodes=nodes)
        seen: set[tuple] = set()
        cache = {}
        limit_reported = False
        reference_limit_reported = False

        def edge(source, target_address, kind, expression=None, attribute=None, metadata=None):
            nonlocal limit_reported
            key = (source.id, target_address, kind, expression, attribute)
            if key in seen:
                return
            if len(graph.edges) >= self.max_edges:
                if not limit_reported:
                    graph.diagnostics.append("Graph edge limit reached.")
                    limit_reported = True
                return
            seen.add(key)
            target = by_address.get(target_address)
            graph.edges.append(
                DependencyEdge(
                    source_entity_id=source.id,
                    target_entity_id=target.id if target else None,
                    target_address=target_address,
                    edge_type=kind,
                    expression=expression,
                    attribute_name=attribute,
                    metadata={**(metadata or {}), "resolved": target is not None},
                )
            )

        for entity in nodes:
            scope = entity.metadata.get("module_path", [])
            parent = by_address.get(scope_address(scope) or "root")
            if entity.entity_type == EntityType.MODULE:
                info = SourceClassifier().classify(entity.metadata.get("source"))
                entity.metadata.update(info.model_dump(mode="json"))
                expression = entity.attributes.get("version")
                version_type = VersionConstraintClassifier().classify(
                    entity.metadata.get("version")
                )
                if expression is not None and literal_string(expression) is None:
                    version_type = "UNKNOWN"
                entity.metadata["version_type"] = str(version_type)
                if parent:
                    edge(parent, entity.address, EdgeType.CALLS_MODULE)
            if entity.entity_type == EntityType.REQUIRED_PROVIDER:
                entity.metadata["version_type"] = str(
                    VersionConstraintClassifier().classify(entity.metadata.get("version"))
                )
                if (
                    entity.attributes.get("version")
                    and literal_string(entity.attributes["version"]) is None
                ):
                    entity.metadata["version_type"] = "UNKNOWN"
                if parent:
                    edge(parent, entity.address, EdgeType.USES_PROVIDER)
            if entity.entity_type == EntityType.PROVIDER:
                edge(
                    entity,
                    qualify(f"required_provider.{entity.name}", scope),
                    EdgeType.USES_PROVIDER,
                    metadata={"binding": "requirement"},
                )
            if (
                entity.entity_type in {EntityType.RESOURCE, EntityType.DATA}
                and "provider" not in entity.attributes
            ):
                provider = entity.resource_type.split("_", 1)[0]
                config_address = qualify(f"provider.{provider}", scope)
                required_address = qualify(f"required_provider.{provider}", scope)
                edge(
                    entity,
                    config_address if config_address in by_address else required_address,
                    EdgeType.USES_PROVIDER,
                    metadata={"binding": "inferred_from_resource_type"},
                )
            for attribute, expression in entity.attributes.items():
                if attribute in {"alias", "configuration_aliases"}:
                    continue
                if attribute in {"source", "version"} and literal_string(expression) is not None:
                    continue
                provider_binding = attribute in {"provider", "providers"}
                bound_names = frozenset(
                    entity.metadata.get("attribute_scopes", {}).get(attribute, [])
                )
                key = (expression, attribute, tuple(scope), provider_binding, bound_names)
                try:
                    if key not in cache:
                        cache[key] = self.extractor.extract(
                            entity.address,
                            expression,
                            attribute,
                            scope,
                            provider=provider_binding,
                            bound_names=bound_names,
                        )
                    references = cache[key]
                except Exception:
                    graph.diagnostics.append(
                        f"Expression could not be analyzed at {entity.address}:{attribute}."
                    )
                    continue
                for reference in references:
                    if len(graph.references) < self.max_edges:
                        graph.references.append(
                            reference.model_copy(update={"source_address": entity.address})
                        )
                    elif not reference_limit_reported:
                        graph.diagnostics.append("Reference limit reached.")
                        reference_limit_reported = True
                    target = reference.target_address
                    if provider_binding and target not in by_address:
                        # An implicit default configuration can resolve to its requirement declaration.
                        local_part = (
                            target.removeprefix(scope_address(scope) + ".") if scope else target
                        )
                        if len(local_part.split(".")) == 2:
                            required = qualify(
                                local_part.replace("provider.", "required_provider.", 1), scope
                            )
                            if required in by_address:
                                target = required
                    metadata = {
                        "traversal": reference.traversal,
                        "target_attribute": reference.target_attribute,
                    }
                    edge(entity, target, reference.reference_type, expression, attribute, metadata)
                    if reference.reference_type == EdgeType.CONSUMES_MODULE_OUTPUT:
                        output_name = reference.target_attribute.split(".")[0]
                        declaration = reference.target_address + ".output." + output_name
                        output_target = (
                            declaration
                            if declaration in by_address
                            else reference.target_address + "." + reference.target_attribute
                        )
                        edge(
                            entity,
                            output_target,
                            EdgeType.REFERENCES,
                            expression,
                            attribute,
                            {**metadata, "module_output": True},
                        )
                    elif reference.reference_type == EdgeType.RESOURCE_DEPENDS_ON:
                        edge(entity, target, EdgeType.REFERENCES, expression, attribute, metadata)
        return graph
