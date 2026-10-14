from pathlib import Path

from app.analyzers.base import ModuleAnalyzer
from app.analyzers.classifiers import (
    GitRefType,
    ModuleSourceType,
    SourceClassifier,
    VersionConstraintClassifier,
    VersionType,
)
from app.graphs.builder import DependencyGraphBuilder
from app.graphs.domain import DependencyGraph
from app.models.enums import Severity
from app.parsers.domain import EntityType, TerraformParseResult
from app.parsers.hcl import HCLTerraformParser
from app.schemas.finding import NormalizedFinding

# Rule identity, severity and remediation are independent of parser/graph implementation.
RULES = {
    "TFSC001": (
        "MISSING_MODULE_VERSION",
        Severity.MEDIUM,
        "Registry module version is missing",
        "Set an exact module version or a bounded version constraint.",
    ),
    "TFSC002": (
        "BROAD_MODULE_VERSION",
        Severity.MEDIUM,
        "Registry module version constraint is broad",
        "Use an exact module version or a constraint with an upper bound.",
    ),
    "TFSC003": (
        "UNPINNED_GIT_MODULE",
        Severity.HIGH,
        "Git module has no pinned reference",
        "Pin the Git source to a full immutable commit SHA.",
    ),
    "TFSC004": (
        "BRANCH_REFERENCED_GIT_MODULE",
        Severity.HIGH,
        "Git module references a mutable branch",
        "Pin the module source to a full immutable commit SHA or a controlled versioned release.",
    ),
    "TFSC005": (
        "TAG_REFERENCED_GIT_MODULE",
        Severity.LOW,
        "Git module references a mutable tag",
        "Pin to a full commit SHA when immutable source selection is required.",
    ),
    "TFSC006": (
        "EXTERNAL_GIT_MODULE",
        Severity.INFO,
        "Module uses an external Git source",
        "Review the source owner and change controls. An external source alone does not imply malicious code.",
    ),
    "TFSC007": (
        "MISSING_PROVIDER_VERSION",
        Severity.MEDIUM,
        "Provider version constraint is missing",
        "Declare a bounded provider version constraint and review the dependency lock file separately.",
    ),
    "TFSC008": (
        "BROAD_PROVIDER_VERSION",
        Severity.MEDIUM,
        "Provider version constraint is broad",
        "Add an upper bound or select an exact provider version.",
    ),
    "TFSC009": (
        "UNBOUNDED_PROVIDER_VERSION",
        Severity.HIGH,
        "Provider version is unbounded",
        "Replace the unrestricted constraint with a bounded or exact version.",
    ),
    "TFSC010": (
        "EXTERNAL_PROVIDER_SOURCE",
        Severity.INFO,
        "Provider uses an external publisher or registry",
        "Review the publisher and registry. This observation does not indicate a malicious provider.",
    ),
    "TFSC011": (
        "MISSING_PROVIDER_SOURCE",
        Severity.MEDIUM,
        "Provider source address is missing",
        "Declare the provider source explicitly in required_providers.",
    ),
}


class SourceTrustAnalyzer(ModuleAnalyzer[list[NormalizedFinding]]):
    def analyze(
        self,
        module_path: Path,
        *,
        parse_result: TerraformParseResult | None = None,
        graph: DependencyGraph | None = None,
    ) -> list[NormalizedFinding]:
        parsed = (
            parse_result
            if parse_result is not None
            else HCLTerraformParser().parse_directory(module_path)
        )
        graph = graph if graph is not None else DependencyGraphBuilder().build(parsed)
        findings = []
        for entity in graph.nodes:
            if entity.entity_type not in {EntityType.MODULE, EntityType.REQUIRED_PROVIDER}:
                continue
            metadata = entity.metadata
            source = metadata.get("source")
            version = metadata.get("version")
            version_type = metadata.get(
                "version_type", VersionConstraintClassifier().classify(version)
            )
            evidence = {
                "source": source,
                "version": version,
                "version_type": str(version_type),
                "module_path": metadata.get("module_path", []),
                "entity_id": str(entity.id),
            }
            rules = []
            if entity.entity_type == EntityType.MODULE:
                info = SourceClassifier().classify(source)
                evidence.update(info.model_dump(mode="json"))
                if info.source_type == ModuleSourceType.REGISTRY:
                    if version_type == VersionType.MISSING:
                        rules.append("TFSC001")
                    elif version_type in {VersionType.BROAD, VersionType.UNBOUNDED}:
                        rules.append("TFSC002")
                if info.source_type in {ModuleSourceType.GIT, ModuleSourceType.GITHUB}:
                    rules.append("TFSC006")
                    ref_rule = {
                        GitRefType.MISSING: "TFSC003",
                        GitRefType.BRANCH: "TFSC004",
                        GitRefType.TAG: "TFSC005",
                    }.get(info.ref_type)
                    if ref_rule:
                        rules.append(ref_rule)
            else:
                version_rule = {
                    VersionType.MISSING: "TFSC007",
                    VersionType.BROAD: "TFSC008",
                    VersionType.UNBOUNDED: "TFSC009",
                }.get(version_type)
                if version_rule:
                    rules.append(version_rule)
                if not source:
                    if "source" not in entity.attributes:
                        rules.append("TFSC011")
                elif not source.startswith("${"):
                    parts = source.split("/")
                    official = (len(parts) == 2 and parts[0] == "hashicorp") or (
                        len(parts) == 3 and parts[:2] == ["registry.terraform.io", "hashicorp"]
                    )
                    if not official:
                        rules.append("TFSC010")
            for rule in rules:
                name, severity, title, guideline = RULES[rule]
                description = title + ". "
                if rule == "TFSC004":
                    description += f"The branch '{evidence['ref']}' may resolve to new code without a configuration change."
                elif rule == "TFSC005":
                    description += (
                        "Version-like tags are a heuristic and can be moved to another commit."
                    )
                elif rule in {"TFSC006", "TFSC010"}:
                    description += (
                        "This is a dependency surface observation, not a maliciousness verdict."
                    )
                else:
                    description += "Source selection may change without a narrowly controlled dependency update."
                findings.append(
                    NormalizedFinding(
                        rule_id=rule,
                        scanner="source_trust",
                        severity=severity,
                        title=title,
                        resource=entity.address,
                        resource_type="module"
                        if entity.entity_type == EntityType.MODULE
                        else "provider",
                        file_path=entity.file_path,
                        line_start=entity.line_start,
                        line_end=entity.line_end,
                        description=description,
                        guideline=guideline,
                        raw_data={**evidence, "rule_name": name},
                    )
                )
        return findings
