"""Data flow edge queries; filtering, counting and pagination run in the database.

Endpoint fields live in GraphEdge.metadata (JSON on SQLite, JSONB on PostgreSQL). The JSON
accessors below compile to JSON_EXTRACT on SQLite and ->> / #>> on PostgreSQL.
"""

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import case, func, or_, select, true
from sqlalchemy.orm import Session

from app.graphs.flow_domain import FLOW_EDGE_TYPES
from app.models.graph_edge import GraphEdge


@dataclass(frozen=True)
class FlowFilter:
    source: str | None = None
    target: str | None = None
    edge_type: str | None = None
    entity_type: str | None = None


class FlowRepository:
    def __init__(self, session: Session):
        self.session = session

    @staticmethod
    def _conditions(scan_id: UUID, filters: FlowFilter | None) -> list:
        metadata = GraphEdge.edge_metadata
        conditions = [GraphEdge.scan_id == scan_id, GraphEdge.edge_type.in_(FLOW_EDGE_TYPES)]
        if filters is None:
            return conditions
        if filters.source is not None:
            conditions.append(metadata["source_address"].as_string() == filters.source)
        if filters.target is not None:
            conditions.append(metadata["target_address"].as_string() == filters.target)
        if filters.edge_type is not None:
            conditions.append(GraphEdge.edge_type == filters.edge_type)
        if filters.entity_type is not None:
            entity_type = str(filters.entity_type)
            conditions.append(
                or_(
                    metadata[("source", "entity_type")].as_string() == entity_type,
                    metadata[("target", "entity_type")].as_string() == entity_type,
                )
            )
        return conditions

    def edges(
        self,
        scan_id: UUID,
        filters: FlowFilter | None = None,
        *,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[GraphEdge]:
        # Same ordering as GraphRepository.edges so pages match the previous in-memory slicing.
        query = (
            select(GraphEdge)
            .where(*self._conditions(scan_id, filters))
            .order_by(
                GraphEdge.source_entity_id,
                GraphEdge.edge_type,
                GraphEdge.target_address,
                GraphEdge.id,
            )
            .offset(offset)
        )
        if limit is not None:
            query = query.limit(limit)
        return list(self.session.scalars(query))

    def summary(self, scan_id: UUID, filters: FlowFilter | None = None) -> dict[str, int]:
        """Counts of all matching edges, before pagination."""
        metadata = GraphEdge.edge_metadata

        def flagged(key: str):
            return func.coalesce(
                func.sum(case((metadata[key].as_boolean() == true(), 1), else_=0)), 0
            )

        total, cross_module, resolved, opaque = self.session.execute(
            select(
                func.count(GraphEdge.id),
                flagged("cross_module"),
                flagged("resolved"),
                flagged("opaque"),
            ).where(*self._conditions(scan_id, filters))
        ).one()
        return {
            "total_flows": int(total),
            "cross_module_flows": int(cross_module),
            # A missing or false "resolved" flag counts as unresolved, as before.
            "unresolved_flows": int(total) - int(resolved),
            "opaque_external_flows": int(opaque),
        }
