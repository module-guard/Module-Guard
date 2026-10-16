import re
from enum import StrEnum
from urllib.parse import parse_qs, urlsplit

from pydantic import BaseModel


def is_official_provider(source: str | None) -> bool:
    if not source:
        return False
    parts = source.split("/")
    return (len(parts) == 2 and parts[0] == "hashicorp") or (
        len(parts) == 3 and parts[:2] == ["registry.terraform.io", "hashicorp"]
    )


class ModuleSourceType(StrEnum):
    REGISTRY = "REGISTRY"
    GITHUB = "GITHUB"
    GIT = "GIT"
    LOCAL = "LOCAL"
    HTTP = "HTTP"
    UNKNOWN = "UNKNOWN"


class GitRefType(StrEnum):
    COMMIT_SHA = "COMMIT_SHA"
    BRANCH = "BRANCH"
    TAG = "TAG"
    UNKNOWN = "UNKNOWN"
    MISSING = "MISSING"


class VersionType(StrEnum):
    EXACT = "EXACT"
    BOUNDED = "BOUNDED"
    BROAD = "BROAD"
    UNBOUNDED = "UNBOUNDED"
    MISSING = "MISSING"
    UNKNOWN = "UNKNOWN"


class SourceInfo(BaseModel):
    source: str | None
    source_type: ModuleSourceType
    ref: str | None = None
    ref_type: GitRefType = GitRefType.MISSING
    immutable: bool = False


class GitRefClassifier:
    def classify(self, ref: str | None) -> GitRefType:
        if not ref:
            return GitRefType.MISSING
        if re.fullmatch(r"(?:[0-9a-fA-F]{40}|[0-9a-fA-F]{64})", ref):
            return GitRefType.COMMIT_SHA
        if ref.startswith("refs/tags/") or re.fullmatch(r"v?\d+\.\d+\.\d+(?:[-+][\w.-]+)?", ref):
            return GitRefType.TAG
        if ref in {
            "main",
            "master",
            "develop",
            "development",
            "dev",
            "trunk",
            "stable",
            "HEAD",
        } or ref.startswith(("refs/heads/", "feature/", "release/", "hotfix/", "bugfix/")):
            return GitRefType.BRANCH
        return GitRefType.UNKNOWN


class SourceClassifier:
    def classify(self, source: str | None) -> SourceInfo:
        kind = ModuleSourceType.UNKNOWN
        if source and not any(marker in source for marker in ("${", "%{")):
            if source.startswith(("./", "../")):
                kind = ModuleSourceType.LOCAL
            elif source.startswith("git::") or source.startswith(("git@", "ssh://", "git://")):
                kind = ModuleSourceType.GIT
            elif re.match(r"^(?:https://)?github\.com/[^/]+/[^/?#]+", source):
                kind = ModuleSourceType.GITHUB
            elif re.fullmatch(r"(?:[\w.-]+(?::\d+)?/)?[\w-]+/[\w-]+/[\w-]+(?://[^?]+)?", source):
                kind = ModuleSourceType.REGISTRY
            elif source.startswith(("https://", "http://")):
                kind = ModuleSourceType.HTTP
        ref = None
        ref_type = GitRefType.MISSING
        if kind in (ModuleSourceType.GIT, ModuleSourceType.GITHUB):
            try:
                values = parse_qs(urlsplit(source.removeprefix("git::")).query).get("ref", [])
                ref = values[0] if len(values) == 1 else None
                ref_type = (
                    GitRefClassifier().classify(ref) if len(values) <= 1 else GitRefType.UNKNOWN
                )
            except ValueError:
                ref_type = GitRefType.UNKNOWN
        return SourceInfo(
            source=source,
            source_type=kind,
            ref=ref,
            ref_type=ref_type,
            immutable=ref_type == GitRefType.COMMIT_SHA,
        )


class VersionConstraintClassifier:
    _version = r"\d+(?:\.\d+){0,2}(?:-[0-9A-Za-z.-]+)?(?:\+[0-9A-Za-z.-]+)?"

    def classify(self, value: str | None) -> VersionType:
        if value is None or not value.strip():
            return VersionType.MISSING
        value = value.strip()
        if value == "*":
            return VersionType.UNBOUNDED
        parts = [part.strip() for part in value.split(",")]
        matches = [re.fullmatch(rf"(=|!=|>=|<=|~>|>|<)?\s*({self._version})", p) for p in parts]
        if not all(matches):
            return VersionType.UNKNOWN
        ops = [match.group(1) or "=" for match in matches]
        if len(parts) == 1 and ops[0] == "=":
            return VersionType.EXACT
        if any(op in {"<", "<=", "~>", "="} for op in ops):
            return VersionType.BOUNDED
        if (
            len(parts) == 1
            and ops[0] == ">="
            and re.fullmatch(r"0(?:\.0){0,2}", matches[0].group(2))
        ):
            return VersionType.UNBOUNDED
        return VersionType.BROAD
