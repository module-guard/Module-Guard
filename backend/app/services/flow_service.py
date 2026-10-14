from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.graphs.search import FlowPathSearch
from app.repositories.graph_repository import GraphRepository
from app.repositories.scan_repository import ScanRepository
from app.schemas.flow import (
    EntityFlowResponse,
    FlowEdgeResponse,
    FlowNode,
    FlowResponse,
    FlowTraceResponse,
)


class FlowService:
    """One graph-edge query per request; bounded adjacency traversal, no per-edge DB queries."""

    def __init__(self, session: Session, settings: Settings | None = None):
        self.repository = GraphRepository(session)
        self.scans = ScanRepository(session)
        self.settings = settings or Settings()

    def load(self, scan_id: UUID) -> list[FlowEdgeResponse]:
        self.scans.get(scan_id)
        return [
            FlowEdgeResponse(
                id=e.id,
                source=e.edge_metadata["source_address"],
                target=e.edge_metadata["target_address"],
                type=e.edge_type,
                expression=e.expression,
                metadata=e.edge_metadata,
            )
            for e in self.repository.edges(scan_id, flow_only=True)
        ]

    @staticmethod
    def nodes(edges: list[FlowEdgeResponse]) -> dict[str, FlowNode]:
        nodes = {}
        for edge in edges:
            for side in ("source", "target"):
                endpoint = edge.metadata[side]
                address = endpoint["address"]
                nodes[address] = FlowNode(
                    id=address,
                    address=address,
                    entity_id=endpoint.get("entity_id"),
                    entity_address=endpoint.get("entity_address"),
                    entity_type=endpoint.get("entity_type", "UNRESOLVED"),
                    attribute_path=endpoint.get("attribute_path"),
                    metadata=endpoint,
                )
        return nodes

    def flows(
        self,
        scan_id: UUID,
        *,
        source: str | None = None,
        target: str | None = None,
        edge_type: str | None = None,
        entity_type: str | None = None,
        limit: int = 20,
        offset: int = 0,
    ) -> FlowResponse:
        edges = [
            e
            for e in self.load(scan_id)
            if (source is None or e.source == source)
            and (target is None or e.target == target)
            and (edge_type is None or e.type == edge_type)
            and (
                entity_type is None
                or any(e.metadata[s]["entity_type"] == entity_type for s in ("source", "target"))
            )
        ]
        summary = {
            "total_flows": len(edges),
            "cross_module_flows": sum(bool(e.metadata.get("cross_module")) for e in edges),
            "unresolved_flows": sum(not e.metadata.get("resolved", False) for e in edges),
            "opaque_external_flows": sum(bool(e.metadata.get("opaque")) for e in edges),
        }
        page = edges[offset : offset + limit]
        return FlowResponse(
            nodes=list(self.nodes(page).values()),
            edges=page,
            summary=summary,
            limit=limit,
            offset=offset,
        )

    def entity(self, scan_id: UUID, address: str) -> EntityFlowResponse:
        edges = self.load(scan_id)
        nodes = self.nodes(edges)
        # Entity lookup is exact; prefixes must not accidentally include another module/resource.
        selected = {
            n.address
            for n in nodes.values()
            if n.address == address
            or n.entity_address == address
            or n.metadata.get("boundary_address") == address
        }
        if not selected:
            entities = self.repository.entities(scan_id)
            if not any(e.address == address for e in entities):
                raise NotFoundError("Flow entity or address not found.")
        upstream = [e for e in edges if e.target in selected]
        downstream = [e for e in edges if e.source in selected]
        cap = 1000
        return EntityFlowResponse(
            entity=address,
            upstream=upstream[:cap],
            downstream=downstream[:cap],
            truncated=len(upstream) > cap or len(downstream) > cap,
        )

    def trace(
        self, scan_id: UUID, source: str, target: str, max_depth: int = 10
    ) -> FlowTraceResponse:
        edges = self.load(scan_id)
        nodes = self.nodes(edges)
        max_depth = min(max_depth, self.settings.max_flow_depth)
        response = FlowTraceResponse(found=False, paths=[], path_edge_ids=[], max_depth=max_depth)
        if source not in nodes or target not in nodes:
            return response
        result = FlowPathSearch(edges).search(
            source,
            lambda address: address == target,
            max_depth=max_depth,
            max_paths=self.settings.max_flow_paths,
            max_steps=self.settings.max_flow_search_steps,
        )
        response.paths = [
            [nodes[source], *[nodes[e.target] for e in path]] for path in result.paths
        ]
        response.path_edge_ids = [[e.id for e in path] for path in result.paths]
        response.explored_steps = result.explored_steps
        response.truncated = result.truncated
        response.found = bool(response.paths)
        return response
