from collections import defaultdict
from uuid import UUID

from app.core.config import Settings
from app.correlation.domain import FlowCandidate, PathQuality
from app.correlation.matcher import FindingEntityMatcher, matches_attribute
from app.correlation.scoring import path_quality
from app.graphs.domain import DependencyGraph
from app.graphs.flow_domain import FLOW_EDGE_TYPES
from app.graphs.search import FlowPathSearch
from app.parsers.domain import EntityType
from app.schemas.finding import NormalizedFinding
from app.schemas.flow import FlowEdgeResponse

SECRET_VALUE_ATTRIBUTES = {
    "aws_ssm_parameter": {"value"},
    "aws_secretsmanager_secret_version": {"secret_string", "secret_binary"},
    "azurerm_key_vault_secret": {"value"},
    "google_secret_manager_secret_version": {"secret_data"},
}
EXTERNAL_TYPES = {"GIT", "GITHUB", "REGISTRY", "HTTP"}


class CorrelationContext:
    """Scan-local indexes and a single bounded traversal pass shared by every rule."""

    def __init__(
        self,
        scan_id: UUID,
        graph: DependencyGraph,
        findings: list[NormalizedFinding],
        settings: Settings | None = None,
    ):
        self.scan_id = scan_id
        self.settings = settings or Settings()
        self.entities = {e.address: e for e in graph.nodes}
        self.entities_by_id = {str(e.id): e for e in graph.nodes}
        self.findings_by_scanner = defaultdict(list)
        self.findings_by_rule = defaultdict(list)
        self.findings_by_resource = defaultdict(list)
        self.trust_by_entity = defaultdict(list)
        self.checkov_by_entity = defaultdict(list)
        self.matcher = FindingEntityMatcher(graph.nodes)
        for finding in findings:
            self.findings_by_scanner[finding.scanner].append(finding)
            self.findings_by_rule[finding.rule_id].append(finding)
            self.findings_by_resource[finding.resource].append(finding)
            if finding.check_result != "FAILED":
                continue
            if finding.scanner == "source_trust":
                entity = self.matcher.source_trust(finding)
                if entity:
                    self.trust_by_entity[entity.address].append(finding)
            elif finding.scanner == "checkov":
                match = self.matcher.checkov(finding)
                if match:
                    self.checkov_by_entity[match.entity.address].append(match)
        self.dependency_edges = [e for e in graph.edges if e.edge_type not in FLOW_EDGE_TYPES]
        self.flows = [
            FlowEdgeResponse(
                id=e.id,
                source=e.metadata["source_address"],
                target=e.metadata["target_address"],
                type=e.edge_type,
                expression=e.expression,
                metadata=e.metadata,
            )
            for e in graph.edges
            if e.edge_type in FLOW_EDGE_TYPES
        ]
        self.search = FlowPathSearch(self.flows)
        self.boundaries = {e.address: e for e in graph.nodes if e.entity_type == EntityType.MODULE}
        self.sources = {}
        self.sinks = {}
        self.sinks_by_category = defaultdict(set)
        self.sources_by_category = defaultdict(set)
        for edge in self.flows:
            source = edge.metadata["source"]
            target = edge.metadata["target"]
            entity = self.entities_by_id.get(source.get("entity_id"))
            # Trust graph identity, not just a loose classification label in metadata.
            external_output = (
                entity is not None
                and entity.entity_type == EntityType.MODULE
                and entity.metadata.get("source_type") in EXTERNAL_TYPES
                and source.get("attribute_path")
                and source.get("external")
            )
            secret_value = (
                entity is not None
                and entity.entity_type == EntityType.DATA
                and source.get("attribute_path")
                in SECRET_VALUE_ATTRIBUTES.get(entity.resource_type, set())
            )
            if external_output or secret_value:
                self.sources[source["address"]] = source
                self.sources_by_category[source.get("source_category")].add(source["address"])
            sink_entity = self.entities_by_id.get(target.get("entity_id"))
            if (
                sink_entity
                and sink_entity.entity_type == EntityType.RESOURCE
                and target.get("attribute_path")
            ):
                self.sinks[target["address"]] = target
                self.sinks_by_category[target.get("sink_category")].add(target["address"])
        self.candidates: list[FlowCandidate] = []
        self.paths_evaluated = 0
        self.traversal_steps = 0
        self.truncated = False
        self.provider_matches = []

    def prepare(self) -> None:
        for address in sorted(self.sources):
            budget = self.settings.max_flow_search_steps - self.traversal_steps
            if budget <= 0:
                self.truncated = True
                break
            found = self.search.search(
                address,
                self.sinks.__contains__,
                max_depth=self.settings.max_flow_depth,
                max_paths=self.settings.max_flow_paths,
                max_steps=budget,
                continue_after_match=True,
            )
            self.traversal_steps += found.explored_steps
            self.truncated |= found.truncated
            for path in found.paths:
                if not path:
                    continue
                self.paths_evaluated += 1
                quality = path_quality(path)
                if quality not in {PathQuality.RESOLVED, PathQuality.OPAQUE}:
                    continue
                source, sink = path[0].metadata["source"], path[-1].metadata["target"]
                matches = [
                    m
                    for m in self.checkov_by_entity[sink["entity_address"]]
                    if matches_attribute(m, sink.get("attribute_path"))
                ]
                self.candidates.append(
                    FlowCandidate(
                        path,
                        source,
                        sink,
                        quality,
                        self.trust_by_entity.get(source.get("entity_address"), []),
                        matches,
                    )
                )
        self.candidates.sort(
            key=lambda c: (
                c.quality != PathQuality.RESOLVED,
                len(c.path),
                c.source["address"],
                c.sink["address"],
            )
        )
        # Only actual USES_PROVIDER edges; no resource-name/provider substring matching.
        bindings = defaultdict(list)
        for edge in self.dependency_edges:
            if (
                edge.edge_type == "USES_PROVIDER"
                and edge.source_entity_id
                and edge.target_entity_id
            ):
                bindings[str(edge.source_entity_id)].append(edge)
        for resource, matches in self.checkov_by_entity.items():
            entity = self.entities[resource]
            for first in bindings[str(entity.id)]:
                target = self.entities_by_id[str(first.target_entity_id)]
                chains = [(target, [first])]
                if target.entity_type == EntityType.PROVIDER:
                    chains = [
                        (self.entities_by_id[str(e.target_entity_id)], [first, e])
                        for e in bindings[str(target.id)]
                    ]
                for provider, chain in chains:
                    if provider.entity_type != EntityType.REQUIRED_PROVIDER:
                        continue
                    trusts = [
                        f
                        for f in self.trust_by_entity[provider.address]
                        if f.rule_id in {"TFSC008", "TFSC009"}
                    ]
                    for trust in trusts:
                        for match in matches:
                            if match.finding.severity in {"HIGH", "CRITICAL"}:
                                self.provider_matches.append((trust, match, chain))
