from app.correlation.domain import Confidence, FlowCandidate, PathQuality
from app.models.enums import Severity

PRIVILEGED_IAM_RULES = frozenset({"CKV_AWS_62", "CKV_AWS_63"})
SEVERITY_WEIGHT = {"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1, "INFO": 0, "UNKNOWN": 0}


def path_quality(path) -> PathQuality:
    opaque = False
    for edge in path:
        for side in ("source", "target"):
            endpoint = edge.metadata.get(side, {})
            if not endpoint.get("resolved"):
                if (
                    endpoint.get("reason") == "external_module"
                    and endpoint.get("external")
                    and endpoint.get("opaque")
                    and endpoint.get("entity_id")
                    and endpoint.get("entity_type") == "MODULE"
                ):
                    opaque = True
                else:
                    return PathQuality.UNRESOLVED
        # Aggregate field projections / loop mappings cannot support precise influence claims.
        source = edge.metadata.get("source", {})
        if edge.metadata.get("collection_iteration") or (
            source.get("entity_type") in {"LOCAL", "VARIABLE"}
            and edge.metadata.get("referenced_attribute")
        ):
            return PathQuality.PARTIAL
    return PathQuality.OPAQUE if opaque else PathQuality.RESOLVED


def confidence_for(candidate: FlowCandidate) -> Confidence:
    if candidate.quality not in {PathQuality.RESOLVED, PathQuality.OPAQUE}:
        return Confidence.LOW
    if candidate.quality == PathQuality.OPAQUE or any(
        e.metadata.get("conditional") for e in candidate.path
    ):
        return Confidence.MEDIUM
    if any(m.confidence != Confidence.HIGH for m in candidate.checkov):
        return Confidence.MEDIUM
    return Confidence.HIGH


def score_flow(rule_id: str, candidate: FlowCandidate) -> tuple[Severity, dict]:
    parts = {
        "mutable_dependency": 3 if candidate.mutable else 0,
        "sink_criticality": {
            "IAM_POLICY": 4,
            "CODE_EXECUTION": 4,
            "CREDENTIAL": 3,
            "SECRET_STORAGE": 3,
            "ENVIRONMENT": 2,
        }.get(candidate.sink_category, 0),
        "checkov": max((SEVERITY_WEIGHT[m.finding.severity] for m in candidate.checkov), default=0),
        "source_trust": max((SEVERITY_WEIGHT[f.severity] for f in candidate.trust), default=0),
        "opaque": int(candidate.quality == PathQuality.OPAQUE),
        "direct_resolved": int(
            len(candidate.path) == 1 and candidate.quality == PathQuality.RESOLVED
        ),
    }
    total = sum(parts.values())
    severity = (
        Severity.CRITICAL
        if total >= 11
        else Severity.HIGH
        if total >= 7
        else Severity.MEDIUM
        if total >= 3
        else Severity.LOW
    )
    # Rule-specific bounds avoid escalating benign pinning or normal secret-delivery patterns.
    if rule_id == "TFCR001":
        privileged = any(
            m.finding.rule_id in PRIVILEGED_IAM_RULES
            and m.finding.severity in {Severity.HIGH, Severity.CRITICAL}
            and m.confidence == Confidence.HIGH
            for m in candidate.checkov
        )
        severity = Severity.CRITICAL if privileged else Severity.HIGH
    elif rule_id == "TFCR002":
        severity = Severity.CRITICAL
    elif rule_id == "TFCR003":
        severity = Severity.HIGH
    elif rule_id in {"TFCR004", "TFCR006"}:
        severity = Severity.MEDIUM if rule_id == "TFCR004" else Severity.LOW
    elif rule_id == "TFCR008":
        severity = (
            Severity.HIGH
            if candidate.sink_category in {"CODE_EXECUTION", "IAM_POLICY", "CREDENTIAL"}
            else Severity.MEDIUM
        )
    if (
        any(m.confidence != Confidence.HIGH for m in candidate.checkov)
        and severity == Severity.CRITICAL
    ):
        severity = Severity.HIGH
    return severity, {"components": parts, "total": total, "policy": rule_id, "version": 1}
