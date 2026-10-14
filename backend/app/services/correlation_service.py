from uuid import UUID

from sqlalchemy.orm import Session

from app.repositories.correlation_repository import CorrelationRepository
from app.schemas.correlation import CorrelationDetail
from app.schemas.finding import FindingResponse


class CorrelationService:
    def __init__(self, session: Session):
        self.repository = CorrelationRepository(session)

    def detail(self, scan_id: UUID, finding_id: UUID) -> CorrelationDetail:
        finding = self.repository.get(scan_id, finding_id)
        evidence = dict(finding.raw_data)
        related = self.repository.related(
            scan_id,
            evidence.get("source_finding_ids", []) + evidence.get("related_finding_ids", []),
        )
        evidence["source_trust"] = [
            FindingResponse.model_validate(f).model_dump(mode="json")
            for f in related
            if f.scanner == "source_trust"
        ]
        evidence["checkov"] = [
            FindingResponse.model_validate(f).model_dump(mode="json")
            for f in related
            if f.scanner == "checkov"
        ]
        ids = evidence.get("flow_edge_ids", []) + evidence.get("dependency_edge_ids", [])
        edges = {str(e.id): e for e in self.repository.edges(scan_id, ids)}
        evidence["edges"] = [
            {
                "id": key,
                "type": edges[key].edge_type,
                "source_entity_id": str(edges[key].source_entity_id)
                if edges[key].source_entity_id
                else None,
                "target_entity_id": str(edges[key].target_entity_id)
                if edges[key].target_entity_id
                else None,
                "expression": edges[key].expression,
                "metadata": edges[key].edge_metadata,
            }
            for key in ids
            if key in edges
        ]
        evidence["missing_edge_ids"] = [key for key in ids if key not in edges]
        evidence["missing_finding_ids"] = sorted(
            set(evidence.get("source_finding_ids", []) + evidence.get("related_finding_ids", []))
            - {str(f.id) for f in related}
        )
        return CorrelationDetail(
            finding=FindingResponse.model_validate(finding),
            evidence=evidence,
            attack_path=evidence.get("attack_path", []),
        )
