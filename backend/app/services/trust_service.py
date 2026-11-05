"""Visible, version-bound comparisons and factual event reconstruction."""

from collections import Counter

from sqlalchemy import select

from app.attestations.signing import canonical
from app.core.exceptions import ConflictError, NotFoundError
from app.models import Finding, GraphEdge, ModuleVersion, ParsedEntity, Scan
from app.models.common import utcnow
from app.schemas.trust import Change, EvidenceLocation, TrustEvent, TrustTimeline, VersionComparison
from app.services.attestation_service import AttestationService, load_policy
from app.services.social_service import find_visible_repository
from app.storage.integrity import digest


def visible_version(session, owner, name, version, viewer):
    repository = find_visible_repository(session, owner, name, viewer)
    query = select(ModuleVersion).where(ModuleVersion.repository_id == repository.id)
    if version is not None:
        query = query.where(ModuleVersion.version == version)
    row = session.scalar(query.order_by(ModuleVersion.created_at.desc(), ModuleVersion.id).limit(1))
    if row is None:
        raise NotFoundError("Version not found.")
    return row


def complete(scan):
    stages = (scan.analysis or {}).get("stages", {}) if scan else {}
    return bool(
        scan
        and scan.status.value == "COMPLETED"
        and not scan.partial_result
        and stages
        and all(s.get("status") == "COMPLETED" for s in stages.values())
    )


def location(row):
    return EvidenceLocation(
        file=row.file_path,
        line=row.line_start,
        address=getattr(row, "address", getattr(row, "resource", None)),
        severity=getattr(row, "severity", None),
        rule=getattr(row, "rule_id", None),
    )


def inventory(session, scan_id):
    def rows(model):
        result = list(
            session.scalars(
                select(model).where(model.scan_id == scan_id).order_by(model.id).limit(10001)
            )
        )
        if len(result) > 10000:
            raise ConflictError("비교 크기 상한을 초과했습니다. 주변 그래프로 탐색해 주세요.")
        return result

    entities = rows(ParsedEntity)
    # Full scoped address + kind + resource type; never just a resource name.
    keys = {r.id: canonical([r.address, r.entity_type, r.resource_type]).decode() for r in entities}
    result = {}
    for r in entities:
        result[("resource", keys[r.id])] = (digest(canonical(r.attributes)), location(r))
    for r in rows(GraphEdge):
        key = canonical(
            [
                keys.get(r.source_entity_id),
                keys.get(r.target_entity_id),
                r.target_address,
                r.edge_type,
                r.attribute_name,
            ]
        ).decode()
        result[("edge", key)] = (
            digest(canonical([r.expression, r.edge_metadata])),
            EvidenceLocation(address=r.target_address),
        )
    # Preserve multiplicity; line shifts alone do not imply a resolved/new finding.
    findings = {}
    for r in rows(Finding):
        key = canonical([r.scanner, r.rule_id, r.resource, r.file_path, r.scope]).decode()
        findings.setdefault(key, []).append(r)
    for key, group in findings.items():
        ordered = sorted(group, key=lambda r: (r.line_start or 0, r.severity.value))
        for i, r in enumerate(ordered):
            result[("finding", f"{key}:{i}")] = (r.severity.value, location(r))
    return result


def compare_versions(session, before, after, limit=50, offset=0):
    a, b = (
        session.get(Scan, before.scan_id) if before.scan_id else None,
        session.get(Scan, after.scan_id) if after.scan_id else None,
    )
    pa, pb = (
        (a.analysis or {}).get("provenance") if a else None,
        (b.analysis or {}).get("provenance") if b else None,
    )
    required = (
        "checkov_version",
        "build_commit",
        "rules_version",
        "severity_rules_version",
        "remediation_rules_version",
    )
    equal = bool(pa and pb and all(pa.get(k) and pb.get(k) for k in required) and pa == pb)
    comparable = complete(a) and complete(b)
    reasons = []
    if not comparable:
        reasons.append("INCOMPLETE_ANALYSIS")
    if not equal:
        reasons.append("TOOL_CONDITIONS_DIFFERENT_OR_UNKNOWN")
    left, right = inventory(session, a.id) if a else {}, inventory(session, b.id) if b else {}
    changes = []
    for kind, key in sorted(left.keys() | right.keys()):
        old, new = left.get((kind, key)), right.get((kind, key))
        state = (
            "added"
            if old is None
            else "removed"
            if new is None
            else "unchanged"
            if old[0] == new[0]
            else "modified"
        )
        if (not comparable or (kind == "finding" and not equal)) and state != "unchanged":
            state = "uncertain"
        changes.append(
            Change(
                key=key,
                kind=kind,
                change=state,
                before=old[1] if old else None,
                after=new[1] if new else None,
            )
        )
    return VersionComparison(
        before_version=before.id,
        after_version=after.id,
        before_scan=before.scan_id,
        after_scan=after.scan_id,
        comparable=comparable,
        tool_conditions_equal=equal,
        manifest_equal=before.manifest_sha256 == after.manifest_sha256
        if before.manifest_sha256 and after.manifest_sha256
        else None,
        reasons=reasons,
        changes=changes[offset : offset + limit],
        total=len(changes),
        next_offset=offset + limit if offset + limit < len(changes) else None,
        counts=dict(Counter(f"{c.kind}:{c.change}" for c in changes)),
    )


def timeline(session, version, store):
    scan = session.get(Scan, version.scan_id) if version.scan_id else None
    events = []

    def add(stage, at, status, **kw):
        if at is not None:
            events.append(
                TrustEvent(
                    id=f"{stage}:{len(events)}",
                    stage=stage,
                    at=at,
                    status=status,
                    digest=version.manifest_sha256,
                    **kw,
                )
            )

    add("수집", version.collected_at, "recorded")
    add("무결성 검증", version.verified_at, version.integrity_status)
    if scan:
        add("검사 시작", scan.started_at, "started", actor=scan.scanner)
        add(
            "검사 결과 저장",
            scan.finished_at,
            "partial" if scan.partial_result else scan.status.value,
            actor=scan.scanner,
            evidence=f"/scans/{scan.id}",
        )
    proof = (
        AttestationService(session, store, load_policy()).describe(scan, version) if scan else None
    )
    if proof:
        for entry in proof.entries:
            # Claimed timestamps cannot become successful historical events.
            if entry.verification.trusted_at:
                add(
                    "서명 시간 증거",
                    entry.verification.trusted_at,
                    entry.verification.status,
                    actor=entry.verification.subject,
                    time_basis="trusted_timestamp",
                )
    return TrustTimeline(
        version_id=version.id,
        scan_id=version.scan_id,
        events=sorted(events, key=lambda e: e.at.timestamp()),
        unrecorded=["digest 생성 시각", "다운로드 클라이언트 바이트 검증", "과거 서명 검증 시각"],
        signature_status=proof.status if proof else "unknown",
        signature_reasons=proof.reasons if proof else [],
        observed_at=utcnow(),
    )
