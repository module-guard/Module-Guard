from uuid import UUID

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy import or_, select

from app.api.dependencies import Limit, Offset, SessionDep
from app.auth.visibility import require_visible_scan
from app.models import Finding, GraphEdge, ParsedEntity, Scan
from app.models.enums import Severity
from app.schemas.risk_paths import RiskEdge, RiskNode, RiskPath, RiskPaths
from app.services.trust_service import complete, location

router = APIRouter(
    prefix="/scans", tags=["risk-paths"], dependencies=[Depends(require_visible_scan)]
)


@router.get("/{scan_id}/risk-paths", response_model=RiskPaths)
def risk_paths(
    scan_id: UUID,
    response: Response,
    session: SessionDep,
    limit: Limit = 20,
    offset: Offset = 0,
    severity: Severity | None = None,
    rule: str | None = Query(None, max_length=255),
    resource: str | None = Query(None, max_length=8192),
):
    response.headers["Cache-Control"] = "no-store"
    query = select(Finding).where(Finding.scan_id == scan_id)
    if severity:
        query = query.where(Finding.severity == severity)
    if rule:
        query = query.where(Finding.rule_id == rule)
    if resource:
        query = query.where(Finding.resource == resource)
    findings = session.scalars(query.order_by(Finding.id).offset(offset).limit(limit + 1)).all()
    paths = []
    for finding in findings[:limit]:
        entities = list(
            session.scalars(
                select(ParsedEntity)
                .where(ParsedEntity.scan_id == scan_id, ParsedEntity.address == finding.resource)
                .limit(2)
            )
        )
        seed_ids = [e.id for e in entities]
        raw = finding.raw_data or {}
        cited = []
        for item in (raw.get("flow_edge_ids", []) + raw.get("dependency_edge_ids", []))[:200]:
            try:
                cited.append(UUID(item))
            except (ValueError, TypeError):
                pass
        edges = list(
            session.scalars(
                select(GraphEdge)
                .where(
                    GraphEdge.scan_id == scan_id,
                    GraphEdge.id.in_(cited)
                    if cited
                    else or_(
                        GraphEdge.source_entity_id.in_(seed_ids),
                        GraphEdge.target_entity_id.in_(seed_ids),
                    ),
                )
                .order_by(GraphEdge.id)
                .limit(201)
            )
        )
        all_ids = (
            set(seed_ids)
            | {e.source_entity_id for e in edges[:200]}
            | {e.target_entity_id for e in edges[:200]}
        )
        nodes = session.scalars(
            select(ParsedEntity).where(
                ParsedEntity.scan_id == scan_id, ParsedEntity.id.in_(all_ids)
            )
        ).all()
        paths.append(
            RiskPath(
                finding_id=finding.id,
                rule=finding.rule_id,
                severity=finding.severity,
                location=location(finding),
                recommendation=finding.guideline,
                nodes=[
                    RiskNode(
                        id=str(e.id),
                        address=e.address,
                        type=e.entity_type,
                        file_path=e.file_path,
                        line_start=e.line_start,
                    )
                    for e in nodes
                ],
                edges=[
                    RiskEdge(
                        id=str(e.id),
                        source=str(e.source_entity_id),
                        target=str(e.target_entity_id) if e.target_entity_id else None,
                        type=e.edge_type,
                        basis="rule_evidence" if cited else "static_adjacent",
                        resolved=bool(e.target_entity_id),
                    )
                    for e in edges[:200]
                ],
                truncated=len(edges) > 200 or len(cited) == 200,
            )
        )
    return RiskPaths(
        scan_id=scan_id,
        incomplete=not complete(session.get(Scan, scan_id)),
        paths=paths,
        next_offset=offset + limit if len(findings) > limit else None,
    )
