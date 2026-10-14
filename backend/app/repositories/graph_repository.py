from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.graphs.domain import DependencyGraph
from app.graphs.flow_domain import FLOW_EDGE_TYPES
from app.models.graph_edge import GraphEdge
from app.models.parsed_entity import ParsedEntity
from app.parsers.domain import EntityType


class GraphRepository:
    def __init__(self, session: Session):
        self.session = session

    def save(self, scan_id: UUID, graph: DependencyGraph) -> None:
        self.session.add_all(
            ParsedEntity(
                id=node.id,
                scan_id=scan_id,
                entity_type=node.entity_type,
                address=node.address,
                name=node.name,
                resource_type=node.resource_type,
                file_path=node.file_path,
                line_start=node.line_start,
                line_end=node.line_end,
                attributes=node.attributes,
                entity_metadata=node.metadata,
            )
            for node in graph.nodes
        )
        # Flush all nodes together before dependent edges; caller owns the single transaction.
        self.session.flush()
        self.session.add_all(
            GraphEdge(
                id=edge.id,
                scan_id=scan_id,
                source_entity_id=edge.source_entity_id,
                target_entity_id=edge.target_entity_id,
                target_address=edge.target_address,
                edge_type=edge.edge_type,
                expression=edge.expression,
                attribute_name=edge.attribute_name,
                edge_metadata=edge.metadata,
            )
            for edge in graph.edges
        )

    def entities(
        self,
        scan_id: UUID,
        limit: int | None = None,
        offset: int = 0,
        entity_type: EntityType | None = None,
        resource_type: str | None = None,
    ) -> list[ParsedEntity]:
        query = select(ParsedEntity).where(ParsedEntity.scan_id == scan_id)
        if entity_type is not None:
            query = query.where(ParsedEntity.entity_type == entity_type)
        if resource_type is not None:
            query = query.where(ParsedEntity.resource_type == resource_type)
        query = query.order_by(ParsedEntity.address).offset(offset)
        if limit is not None:
            query = query.limit(limit)
        return list(self.session.scalars(query))

    def edges(self, scan_id: UUID, flow_only: bool = False) -> list[GraphEdge]:
        return list(
            self.session.scalars(
                select(GraphEdge)
                .where(GraphEdge.scan_id == scan_id)
                .where(GraphEdge.edge_type.in_(FLOW_EDGE_TYPES) if flow_only else True)
                .order_by(
                    GraphEdge.source_entity_id,
                    GraphEdge.edge_type,
                    GraphEdge.target_address,
                    GraphEdge.id,
                )
            )
        )
