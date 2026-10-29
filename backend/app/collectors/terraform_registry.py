import json
import re
import shutil
from pathlib import Path

import httpx

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.github import GitHubCollector
from app.collectors.source_detector import detect_source, normalize_github_url
from app.core.config import Settings
from app.core.exceptions import (
    RegistryModuleNotFoundError,
    RepositoryCloneError,
    UnsupportedSourceError,
)
from app.models.enums import SourceType

# Registry metadata only ever reports released semantic versions; anything else is refused
# before it reaches a URL or a Git tag.
VERSION = re.compile(r"[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5}")
# The metadata document is tiny; a larger body is treated as an untrusted response.
MAX_RESPONSE_BYTES = 1024 * 1024


class TerraformRegistryCollector(SourceCollector):
    """Resolve a Registry address to its backing GitHub repository, then reuse the Git clone.

    The module is never downloaded from the Registry itself: only the small JSON metadata
    document is read, and the actual source is cloned from GitHub by `GitHubCollector`.
    """

    def __init__(
        self,
        settings: Settings,
        github: GitHubCollector | None = None,
        transport: httpx.BaseTransport | None = None,
    ):
        self.settings = settings
        self.github = github or GitHubCollector(settings)
        # Tests inject an httpx.MockTransport; production uses httpx's default transport.
        self._transport = transport

    def supports(self, source: str) -> bool:
        try:
            return detect_source(source).source_type == SourceType.TERRAFORM_REGISTRY
        except UnsupportedSourceError:
            return False

    def collect(self, source: str, destination: Path, ref: str | None = None) -> CollectedModule:
        identity = detect_source(source)
        if identity.source_type != SourceType.TERRAFORM_REGISTRY:
            raise UnsupportedSourceError("Use namespace/name/provider for a Registry module.")
        if ref is not None and not VERSION.fullmatch(ref):
            raise UnsupportedSourceError("Pin a Registry version like 1.2.3.")
        metadata = self._lookup(identity.owner, identity.repository_name, identity.provider, ref)
        github_url = normalize_github_url(metadata["source"])
        collected = self._clone(github_url, destination, metadata["version"])
        # The stored source is the Git clone, but the module is recorded as a Registry import.
        return CollectedModule(
            SourceType.TERRAFORM_REGISTRY,
            identity.source_url,
            collected.local_path,
            collected.repository_name,
            collected.owner,
            collected.branch,
            collected.commit_hash,
            repository_url=github_url,
            source_version=metadata["version"],
        )

    def _lookup(self, namespace: str, name: str, provider: str, version: str | None) -> dict:
        """Read source repository and version from the Registry metadata document."""
        path = f"/v1/modules/{namespace}/{name}/{provider}"
        if version is not None:
            path += f"/{version}"
        data = self._get_json(path)
        source = data.get("source")
        resolved = data.get("version")
        if not isinstance(source, str) or not isinstance(resolved, str):
            raise RegistryModuleNotFoundError("Registry module metadata is incomplete.")
        if not VERSION.fullmatch(resolved):
            raise RegistryModuleNotFoundError("Registry module has no usable release version.")
        if "github.com" not in source:
            # A non-GitHub backing source is unsupported; normalize_github_url refuses it below.
            raise UnsupportedSourceError("Only GitHub-backed Registry modules are supported.")
        return {"source": source, "version": resolved}

    def _get_json(self, path: str) -> dict:
        try:
            with httpx.Client(
                base_url=self.settings.registry_base_url,
                timeout=self.settings.registry_timeout_seconds,
                follow_redirects=False,
                transport=self._transport,
            ) as client:
                with client.stream("GET", path) as response:
                    if response.status_code == 404:
                        raise RegistryModuleNotFoundError("Registry module not found.")
                    # Any redirect (follow_redirects is off) or other status is upstream failure;
                    # the host is never allowed to change under us.
                    if response.status_code != 200:
                        raise RepositoryCloneError("Unable to read the Registry module metadata.")
                    body = bytearray()
                    for chunk in response.iter_bytes():
                        body.extend(chunk)
                        if len(body) > MAX_RESPONSE_BYTES:
                            raise RepositoryCloneError("Registry response is too large.")
        except httpx.HTTPError:
            # Never surface transport-level text: it may echo attacker-chosen content.
            raise RepositoryCloneError("Unable to reach the Terraform Registry.") from None
        try:
            data = json.loads(body)
        except ValueError:
            raise RepositoryCloneError("Registry response was not valid JSON.") from None
        if not isinstance(data, dict):
            raise RepositoryCloneError("Registry response had an unexpected shape.")
        return data

    def _clone(self, github_url: str, destination: Path, version: str) -> CollectedModule:
        """Published modules tag releases as `vX.Y.Z` or `X.Y.Z`; try both before giving up."""
        for tag in (f"v{version}", version):
            try:
                return self.github.collect(github_url, destination, ref=tag)
            except RepositoryCloneError:
                # GitHubCollector leaves a partial destination on failure; clear it before retry.
                if destination.exists() or destination.is_symlink():
                    shutil.rmtree(destination, ignore_errors=True)
        raise RepositoryCloneError("Unable to fetch the module version from its source repository.")
