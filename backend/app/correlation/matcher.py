"""Conservative mapping: module identity and source evidence, never substring joins."""

import re
from collections import defaultdict
from pathlib import PurePosixPath

from app.correlation.domain import Confidence, FindingMatch
from app.parsers.domain import EntityType, TerraformEntity
from app.schemas.finding import NormalizedFinding


def normalize_address(address: str) -> str:
    # Instance indexes aggregate to declaration addresses, just as the static graph does.
    return re.sub(r'\[(?:\d+|"(?:[^"\\]|\\.)*")\]', "", address)


def normalize_file(path: str | None) -> str | None:
    if not path or "\\" in path or ".." in PurePosixPath(path).parts:
        return None
    return str(PurePosixPath(path)).lstrip("/")


def normalize_attribute(value: str) -> str:
    parts = re.sub(r"\[[^\]]*\]", "", value).replace("/", ".").split(".")
    return ".".join(p for p in parts if p and not p.isdigit())


class FindingEntityMatcher:
    def __init__(self, entities: list[TerraformEntity]):
        self.by_address = {e.address: e for e in entities}
        self.by_local_address = defaultdict(list)
        for entity in entities:
            if entity.entity_type in {EntityType.RESOURCE, EntityType.DATA}:
                self.by_local_address[".".join(entity.address.split(".")[-2:])].append(entity)

    @staticmethod
    def location_agrees(finding: NormalizedFinding, entity: TerraformEntity) -> bool:
        if finding.file_path and normalize_file(finding.file_path) != normalize_file(
            entity.file_path
        ):
            return False
        if finding.line_start is not None and entity.line_start is not None:
            end = finding.line_end or finding.line_start
            if end < entity.line_start or finding.line_start > (
                entity.line_end or entity.line_start
            ):
                return False
        return True

    def source_trust(self, finding: NormalizedFinding) -> TerraformEntity | None:
        entity = self.by_address.get(finding.resource or "")
        if entity is None or entity.entity_type not in {
            EntityType.MODULE,
            EntityType.REQUIRED_PROVIDER,
        }:
            return None
        if finding.raw_data.get("entity_id") not in (None, str(entity.id)):
            return None
        if finding.raw_data.get(
            "module_path", entity.metadata.get("module_path", [])
        ) != entity.metadata.get("module_path", []):
            return None
        return entity if self.location_agrees(finding, entity) else None

    def checkov(self, finding: NormalizedFinding) -> FindingMatch | None:
        address = normalize_address(finding.resource or "")
        entity = self.by_address.get(address)
        method = "exact_address"
        confidence = Confidence.HIGH
        if entity is None or not self.location_agrees(finding, entity):
            # Never erase an explicit module path to force a match in another instance.
            if address.startswith("module.") or not finding.file_path:
                return None
            matches = [
                e
                for e in self.by_local_address.get(address, [])
                if self.location_agrees(finding, e)
            ]
            if len(matches) != 1:
                return None
            entity = matches[0]
            method = "unique_local_address_and_location"
            confidence = Confidence.MEDIUM
        if entity.entity_type != EntityType.RESOURCE:
            return None
        raw = finding.raw_data
        evaluated = raw.get("evaluated_keys")
        if evaluated is None:
            check_result = raw.get("check_result", {})
            evaluated = (
                check_result.get("evaluated_keys", []) if isinstance(check_result, dict) else []
            )
        if isinstance(evaluated, str):
            evaluated = [evaluated]
        attributes = (
            tuple(normalize_attribute(a) for a in evaluated if isinstance(a, str))
            if isinstance(evaluated, list)
            else ()
        )
        return FindingMatch(finding, entity, confidence, method, attributes)


def matches_attribute(match: FindingMatch, attribute: str | None) -> bool:
    if not match.attributes:
        return True  # Explicitly recorded as resource-level evidence.
    if not attribute:
        return False
    target = normalize_attribute(attribute)
    return any(
        a == target or a.startswith(target + ".") or target.startswith(a + ".")
        for a in match.attributes
        if a
    )
