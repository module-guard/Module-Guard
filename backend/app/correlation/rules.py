from abc import ABC, abstractmethod

from app.correlation.context import CorrelationContext
from app.correlation.domain import Confidence, FlowCandidate, PathQuality
from app.correlation.scoring import confidence_for, score_flow
from app.parsers.domain import EntityType
from app.schemas.finding import NormalizedFinding

PRIORITIES = {
    "TFCR002": 0,
    "TFCR001": 1,
    "TFCR003": 2,
    "TFCR004": 3,
    "TFCR005": 4,
    "TFCR008": 5,
    "TFCR006": 6,
    "TFCR007": 7,
}
RULES = {
    "TFCR001": (
        "MUTABLE_EXTERNAL_MODULE_TO_IAM_POLICY",
        "Mutable external module influences an IAM policy",
        "Pin the Git module to a full immutable commit SHA; review the influencing output; restrict IAM actions and resources to least privilege.",
    ),
    "TFCR002": (
        "MUTABLE_EXTERNAL_MODULE_TO_CODE_EXECUTION",
        "Mutable external module influences instance bootstrap configuration",
        "Pin the Git module to a full immutable commit SHA and review the output used by instance bootstrap code.",
    ),
    "TFCR003": (
        "SECRET_SOURCE_TO_CODE_EXECUTION",
        "Secret source reaches instance bootstrap configuration",
        "Review this potential secret exposure surface. Prefer retrieving secrets at runtime with narrowly scoped identity and avoid embedding them in bootstrap content.",
    ),
    "TFCR004": (
        "SECRET_SOURCE_TO_ENVIRONMENT",
        "Secret source reaches an environment configuration",
        "Review access to environment configuration, logs and deployment state. Consider runtime secret retrieval where appropriate.",
    ),
    "TFCR005": (
        "MUTABLE_MODULE_TO_CHECKOV_FINDING",
        "Mutable external module influences a resource with a security configuration finding",
        "Pin the module source and inspect the influencing attribute together with the linked Checkov finding; remediate the configuration independently.",
    ),
    "TFCR006": (
        "EXTERNAL_MODULE_TO_PRIVILEGED_RESOURCE",
        "Pinned external module influences a sensitive resource attribute",
        "Review the pinned dependency and its output contract. Keep immutable references and review deliberate dependency updates.",
    ),
    "TFCR007": (
        "BROAD_PROVIDER_WITH_SECURITY_FINDING",
        "Broad provider constraint accompanies a security-sensitive resource finding",
        "Bound the provider version constraint, review the dependency lock file, and remediate the linked Checkov finding separately.",
    ),
    "TFCR008": (
        "OPAQUE_EXTERNAL_FLOW_TO_SENSITIVE_SINK",
        "Opaque external module output reaches a sensitive sink",
        "Review the external output and implementation before trusting it at this sink. Pin dependency versions and retain reviewable source provenance.",
    ),
}
SENSITIVE = {
    "IAM_POLICY",
    "CODE_EXECUTION",
    "CREDENTIAL",
    "SECRET_STORAGE",
    "ENVIRONMENT",
    "COMMAND",
    "NETWORK",
}


class CorrelationRule(ABC):
    rule_id: str

    @abstractmethod
    def evaluate(self, context: CorrelationContext) -> list[NormalizedFinding]: ...


def immutable_source(context: CorrelationContext, candidate: FlowCandidate) -> bool:
    entity = context.entities.get(candidate.source.get("entity_address"))
    return bool(entity and entity.metadata.get("immutable"))


class FlowCorrelationRule(CorrelationRule):
    def __init__(self, rule_id: str):
        self.rule_id = rule_id

    def matches(self, context: CorrelationContext, candidate: FlowCandidate) -> bool:
        category = candidate.sink_category
        external = candidate.source.get("external", False)
        secret = candidate.source.get("source_category") == "SECRET_SOURCE"
        pinned = immutable_source(context, candidate)
        mutable = candidate.mutable and not pinned
        return {
            "TFCR001": mutable and category == "IAM_POLICY",
            "TFCR002": mutable and category == "CODE_EXECUTION",
            "TFCR003": secret and category == "CODE_EXECUTION",
            "TFCR004": secret and category == "ENVIRONMENT",
            "TFCR005": mutable and bool(candidate.checkov),
            "TFCR006": external
            and pinned
            and category in {"IAM_POLICY", "CODE_EXECUTION", "CREDENTIAL", "SECRET_STORAGE"}
            and any(f.rule_id == "TFSC006" for f in candidate.trust),
            # Safe SHA pins use the lower-severity observation rather than generic opaque escalation.
            "TFCR008": external
            and not pinned
            and candidate.quality == PathQuality.OPAQUE
            and category in SENSITIVE,
        }.get(self.rule_id, False)

    def evaluate(self, context: CorrelationContext) -> list[NormalizedFinding]:
        return [self.finding(context, c) for c in context.candidates if self.matches(context, c)]

    def finding(self, context: CorrelationContext, candidate: FlowCandidate) -> NormalizedFinding:
        name, title, guideline = RULES[self.rule_id]
        severity, score = score_flow(self.rule_id, candidate)
        confidence = confidence_for(candidate)
        sink = context.entities[candidate.sink["entity_address"]]
        trust = [
            f for f in candidate.trust if f.rule_id in {"TFSC003", "TFSC004", "TFSC006", "TFSC005"}
        ]
        mutable_trust = [f for f in trust if f.rule_id in {"TFSC003", "TFSC004"}]
        primary_trust = mutable_trust or trust
        path = [candidate.path[0].source, *[e.target for e in candidate.path]]
        evidence = {
            "schema_version": 1,
            "rule_name": name,
            "source": candidate.source["address"],
            "sink": candidate.sink["address"],
            "source_entity": candidate.source.get("entity_address"),
            "sink_entity": sink.address,
            "source_attribute": candidate.source.get("attribute_path"),
            "sink_attribute": candidate.sink.get("attribute_path"),
            "source_finding_ids": [str(f.id) for f in trust],
            "related_finding_ids": [str(m.finding.id) for m in candidate.checkov],
            "source_trust_rule": primary_trust[0].rule_id if primary_trust else None,
            "checkov_rule": candidate.checkov[0].finding.rule_id if candidate.checkov else None,
            "flow_edge_ids": [str(e.id) for e in candidate.path],
            "dependency_edge_ids": [],
            "flow_path": path,
            "path_quality": candidate.quality.value,
            "confidence": confidence.value,
            "score": score,
            "source_endpoint": candidate.source,
            "sink_endpoint": candidate.sink,
            "checkov_mappings": [
                {
                    "finding_id": str(m.finding.id),
                    "entity": m.entity.address,
                    "method": m.method,
                    "confidence": m.confidence.value,
                    "level": "attribute" if m.attributes else "resource",
                    "attributes": list(m.attributes),
                }
                for m in candidate.checkov
            ],
            "limitations": [
                "Static potential influence, not runtime evaluation or proof of exploitation."
            ],
        }
        evidence["attack_path"] = [
            *[
                {
                    "type": "SOURCE_TRUST",
                    "address": f.resource,
                    "rule": f.rule_id,
                    "finding_id": str(f.id),
                }
                for f in primary_trust
            ],
            *[{"type": "FLOW", "address": address} for address in path[:-1]],
            {"type": "SINK", "address": path[-1], "category": candidate.sink_category},
            *[
                {
                    "type": "CHECKOV",
                    "address": sink.address,
                    "rule": m.finding.rule_id,
                    "finding_id": str(m.finding.id),
                }
                for m in candidate.checkov
            ],
        ]
        description = (
            f'Data from "{path[0]}" flows through the recorded graph path into "{path[-1]}". '
        )
        if candidate.mutable:
            description += "The associated Git module is unpinned or references a mutable branch, increasing supply-chain variability. "
        if candidate.source.get("source_category") == "SECRET_SOURCE":
            description += "This is a potential secret exposure surface and may be an intentional delivery pattern; it is not evidence of a credential leak. "
        if candidate.checkov:
            description += "Checkov independently reports a configuration issue on the mapped resource/attribute; this does not establish that the dependency caused it. "
        if candidate.quality == PathQuality.OPAQUE:
            description += (
                "The external module implementation is opaque and was not downloaded or analyzed. "
            )
        return NormalizedFinding(
            rule_id=self.rule_id,
            scanner="correlation",
            severity=severity,
            title=title,
            resource=sink.address,
            resource_type=sink.resource_type,
            file_path=sink.file_path,
            line_start=sink.line_start,
            line_end=sink.line_end,
            description=description,
            guideline=guideline,
            raw_data=evidence,
        )


class ProviderCorrelationRule(CorrelationRule):
    rule_id = "TFCR007"

    def evaluate(self, context: CorrelationContext) -> list[NormalizedFinding]:
        results = []
        for trust, match, edges in context.provider_matches:
            entity = match.entity
            if entity.entity_type != EntityType.RESOURCE:
                continue
            evidence = {
                "schema_version": 1,
                "rule_name": RULES[self.rule_id][0],
                "source": trust.resource,
                "sink": entity.address,
                "source_entity": trust.resource,
                "sink_entity": entity.address,
                "source_finding_ids": [str(trust.id)],
                "related_finding_ids": [str(match.finding.id)],
                "source_trust_rule": trust.rule_id,
                "checkov_rule": match.finding.rule_id,
                "flow_edge_ids": [],
                "flow_path": [],
                "dependency_edge_ids": [str(e.id) for e in edges],
                "path_quality": "RESOLVED",
                "confidence": Confidence.MEDIUM.value,
                "relationship": "USES_PROVIDER",
                "mapping_method": match.method,
                "mapping_confidence": match.confidence.value,
                "attack_path": [
                    {"type": "SOURCE_TRUST", "address": trust.resource, "rule": trust.rule_id},
                    {
                        "type": "DEPENDENCY",
                        "address": entity.address,
                        "relationship": "USES_PROVIDER",
                    },
                    {"type": "CHECKOV", "address": entity.address, "rule": match.finding.rule_id},
                ],
            }
            results.append(
                NormalizedFinding(
                    rule_id=self.rule_id,
                    scanner="correlation",
                    severity="MEDIUM",
                    title=RULES[self.rule_id][1],
                    resource=entity.address,
                    resource_type=entity.resource_type,
                    file_path=entity.file_path,
                    line_start=entity.line_start,
                    line_end=entity.line_end,
                    description="Broad provider version constraints increase supply-chain variability around resources that already contain security-sensitive configuration findings. The provider relationship is supported by dependency edges; no causal claim is made.",
                    guideline=RULES[self.rule_id][2],
                    raw_data=evidence,
                )
            )
        return results


def default_rules() -> list[CorrelationRule]:
    return [FlowCorrelationRule(f"TFCR{i:03d}") for i in (1, 2, 3, 4, 5, 6, 8)] + [
        ProviderCorrelationRule()
    ]
