from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import Settings
from app.core.exceptions import NotFoundError
from app.graphs.search import FlowPathSearch
from app.models.graph_edge import GraphEdge
from app.repositories.flow_repository import FlowFilter, FlowRepository
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
    """/flows filters and pages in SQL; entity/trace load one scan's flow edges once and
    traverse an in-memory adjacency index. No per-edge DB queries."""

    def __init__(self, session: Session, settings: Settings | None = None):
        self.repository = GraphRepository(session)
        self.flow_repository = FlowRepository(session)
        self.scans = ScanRepository(session)
        self.settings = settings or Settings()

    @staticmethod
    def edge(e: GraphEdge) -> FlowEdgeResponse:
        return FlowEdgeResponse(
            id=e.id,
            source=e.edge_metadata["source_address"],
            target=e.edge_metadata["target_address"],
            type=e.edge_type,
            expression=e.expression,
            metadata=e.edge_metadata,
        )

    def load(self, scan_id: UUID) -> list[FlowEdgeResponse]:
        self.scans.get(scan_id)
        return [self.edge(e) for e in self.flow_repository.edges(scan_id)]

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
                    source_category=endpoint.get("source_category"),
                    sink_category=endpoint.get("sink_category"),
                    opaque=bool(endpoint.get("opaque", False)),
                    external=bool(endpoint.get("external", False)),
                    resolved=bool(endpoint.get("resolved", True)),
                    sensitive_input=bool(endpoint.get("sensitive_input", False)),
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
        self.scans.get(scan_id)
        filters = FlowFilter(
            source=source, target=target, edge_type=edge_type, entity_type=entity_type
        )
        summary = self.flow_repository.summary(scan_id, filters)
        page = [
            self.edge(e)
            for e in self.flow_repository.edges(scan_id, filters, limit=limit, offset=offset)
        ]
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
        upstream_page, downstream_page = upstream[:cap], downstream[:cap]
        return EntityFlowResponse(
            entity=address,
            upstream=upstream_page,
            downstream=downstream_page,
            truncated=len(upstream) > cap or len(downstream) > cap,
            nodes=list(self.nodes([*upstream_page, *downstream_page]).values()),
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
        response.path_edges = [list(path) for path in result.paths]
        response.explored_steps = result.explored_steps
        response.truncated = result.truncated
        response.found = bool(response.paths)
        return response
