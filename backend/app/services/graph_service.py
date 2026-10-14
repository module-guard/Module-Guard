from uuid import UUID

from sqlalchemy.orm import Session

from app.graphs.flow_domain import FLOW_EDGE_TYPES
from app.parsers.domain import EntityType, scope_address
from app.repositories.graph_repository import GraphRepository
from app.repositories.scan_repository import ScanRepository
from app.schemas.graph import (
    DependencyResponse,
    GraphEdgeResponse,
    GraphNodeResponse,
    GraphResponse,
    ModuleDependency,
    ProviderDependency,
)


class GraphService:
    def __init__(self, session: Session):
        self.repository = GraphRepository(session)
        self.scans = ScanRepository(session)

    def graph(self, scan_id: UUID) -> GraphResponse:
        scan = self.scans.get(scan_id)
        entities = self.repository.entities(scan_id)
        edges = [e for e in self.repository.edges(scan_id) if e.edge_type not in FLOW_EDGE_TYPES]
        nodes = [
            GraphNodeResponse(
                id=str(e.id),
                address=e.address,
                type=e.entity_type,
                metadata={**e.entity_metadata, "resolved": True},
                file_path=e.file_path,
                line_start=e.line_start,
                line_end=e.line_end,
            )
            for e in entities
        ]
        placeholders = {}
        responses = []
        for edge in edges:
            target = str(edge.target_entity_id) if edge.target_entity_id else None
            if target is None and edge.target_address:
                target = "unresolved:" + edge.target_address
                placeholders[target] = GraphNodeResponse(
                    id=target,
                    address=edge.target_address,
                    type="UNRESOLVED",
                    metadata={"resolved": False, "declared": False},
                )
            responses.append(
                GraphEdgeResponse(
                    id=edge.id,
                    source=str(edge.source_entity_id),
                    target=target,
                    target_address=edge.target_address,
                    type=edge.edge_type,
                    expression=edge.expression,
                    attribute_name=edge.attribute_name,
                    metadata=edge.edge_metadata,
                )
            )
        return GraphResponse(
            analysis=scan.analysis, nodes=nodes + list(placeholders.values()), edges=responses
        )

    def dependencies(self, scan_id: UUID) -> DependencyResponse:
        scan = self.scans.get(scan_id)
        entities = self.repository.entities(scan_id)
        aliases: dict[tuple, list[str]] = {}
        for entity in entities:
            if entity.entity_type == EntityType.PROVIDER and entity.entity_metadata.get("alias"):
                key = (scope_address(entity.entity_metadata.get("module_path", [])), entity.name)
                aliases.setdefault(key, []).append(entity.entity_metadata["alias"])
        modules = []
        providers = []
        for entity in entities:
            metadata = entity.entity_metadata
            scope = metadata.get("module_path", [])
            if entity.entity_type == EntityType.MODULE:
                modules.append(
                    ModuleDependency(
                        address=entity.address,
                        source=metadata.get("source"),
                        source_type=metadata.get("source_type", "UNKNOWN"),
                        version=metadata.get("version"),
                        version_type=metadata.get("version_type", "UNKNOWN"),
                        ref=metadata.get("ref"),
                        ref_type=metadata.get("ref_type", "UNKNOWN"),
                        immutable=metadata.get("immutable", False),
                        module_path=scope,
                        expansion=metadata.get("expansion", "unknown"),
                    )
                )
            elif entity.entity_type == EntityType.REQUIRED_PROVIDER:
                providers.append(
                    ProviderDependency(
                        address=entity.address,
                        name=entity.name,
                        source=metadata.get("source"),
                        version=metadata.get("version"),
                        version_type=metadata.get("version_type", "UNKNOWN"),
                        module_path=scope,
                        aliases=aliases.get((scope_address(scope), entity.name), []),
                    )
                )
        return DependencyResponse(modules=modules, providers=providers, analysis=scan.analysis)
