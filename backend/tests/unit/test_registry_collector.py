import httpx
import pytest

from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.terraform_registry import MAX_RESPONSE_BYTES, TerraformRegistryCollector
from app.core.exceptions import (
    RegistryModuleNotFoundError,
    RepositoryCloneError,
    UnsupportedSourceError,
)
from app.models.enums import SourceType

ADDRESS = "terraform-aws-modules/vpc/aws"
GITHUB = "https://github.com/terraform-aws-modules/terraform-aws-vpc"


class FakeGitHub(SourceCollector):
    """Stands in for GitHubCollector: records the refs it is asked for, no real clone."""

    def __init__(self, fail_refs=()):
        self.fail_refs = set(fail_refs)
        self.refs = []

    def supports(self, source):
        return True

    def collect(self, source, destination, ref=None):
        self.refs.append(ref)
        if ref in self.fail_refs:
            # GitHubCollector may leave a partial destination; mimic that on failure.
            destination.mkdir(parents=True, exist_ok=True)
            raise RepositoryCloneError("Unable to clone.")
        destination.mkdir(parents=True, exist_ok=True)
        (destination / "main.tf").write_text("x")
        return CollectedModule(
            SourceType.GITHUB,
            source,
            destination,
            "terraform-aws-vpc",
            "terraform-aws-modules",
            "main",
            "a" * 40,
        )


def metadata_handler(source=GITHUB, version="5.8.1"):
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"source": source, "version": version})

    return handler


def make_collector(settings, handler, github=None):
    return TerraformRegistryCollector(
        settings, github=github or FakeGitHub(), transport=httpx.MockTransport(handler)
    )


def test_collects_latest_version(settings, tmp_path):
    github = FakeGitHub()
    collector = make_collector(settings, metadata_handler(version="5.8.1"), github)
    collected = collector.collect(ADDRESS, tmp_path / "m")
    assert collected.source_type == SourceType.TERRAFORM_REGISTRY
    assert collected.source_url == ADDRESS
    assert collected.commit_hash == "a" * 40
    # The vX.Y.Z tag is tried first and here it succeeds.
    assert github.refs == ["v5.8.1"]


def test_pinned_version_is_requested(settings, tmp_path):
    seen = {}

    def handler(request):
        seen["path"] = request.url.path
        return httpx.Response(200, json={"source": GITHUB, "version": "5.8.0"})

    github = FakeGitHub()
    collector = make_collector(settings, handler, github)
    collector.collect(ADDRESS, tmp_path / "m", "5.8.0")
    assert seen["path"] == "/v1/modules/terraform-aws-modules/vpc/aws/5.8.0"
    assert github.refs == ["v5.8.0"]


def test_non_github_source_is_rejected(settings, tmp_path):
    collector = make_collector(settings, metadata_handler(source="https://gitlab.com/a/b"))
    with pytest.raises(UnsupportedSourceError):
        collector.collect(ADDRESS, tmp_path / "m")


def test_unknown_module_is_not_found(settings, tmp_path):
    collector = make_collector(
        settings, lambda request: httpx.Response(404, json={"errors": ["x"]})
    )
    with pytest.raises(RegistryModuleNotFoundError):
        collector.collect(ADDRESS, tmp_path / "m")


def test_redirects_are_refused(settings, tmp_path):
    collector = make_collector(
        settings, lambda request: httpx.Response(302, headers={"location": "https://evil.example"})
    )
    with pytest.raises(RepositoryCloneError):
        collector.collect(ADDRESS, tmp_path / "m")


def test_oversized_response_is_refused(settings, tmp_path):
    big = b'{"source":"' + b"a" * (MAX_RESPONSE_BYTES + 16) + b'"}'
    collector = make_collector(
        settings,
        lambda request: httpx.Response(
            200, content=big, headers={"content-type": "application/json"}
        ),
    )
    with pytest.raises(RepositoryCloneError):
        collector.collect(ADDRESS, tmp_path / "m")


@pytest.mark.parametrize("version", ["5", "5.8", "1.2.3.4", "v5.8.1", "latest", "5.8.x"])
def test_invalid_version_never_reaches_the_network(settings, tmp_path, version):
    def handler(request):
        raise AssertionError("the Registry must not be contacted for a malformed version")

    collector = make_collector(settings, handler)
    with pytest.raises(UnsupportedSourceError):
        collector.collect(ADDRESS, tmp_path / "m", version)


def test_plain_tag_is_tried_when_the_v_prefixed_tag_is_missing(settings, tmp_path):
    github = FakeGitHub(fail_refs={"v5.8.1"})
    collector = make_collector(settings, metadata_handler(version="5.8.1"), github)
    collected = collector.collect(ADDRESS, tmp_path / "m")
    assert collected.commit_hash == "a" * 40
    assert github.refs == ["v5.8.1", "5.8.1"]


def test_clone_failure_on_both_tags_is_a_clone_error(settings, tmp_path):
    github = FakeGitHub(fail_refs={"v5.8.1", "5.8.1"})
    collector = make_collector(settings, metadata_handler(version="5.8.1"), github)
    with pytest.raises(RepositoryCloneError):
        collector.collect(ADDRESS, tmp_path / "m")
    assert github.refs == ["v5.8.1", "5.8.1"]


def test_non_registry_address_is_rejected(settings, tmp_path):
    collector = make_collector(settings, metadata_handler())
    with pytest.raises(UnsupportedSourceError):
        collector.collect("https://github.com/org/repo", tmp_path / "m")


def test_malformed_json_is_a_clone_error(settings, tmp_path):
    collector = make_collector(settings, lambda request: httpx.Response(200, content=b"not json"))
    with pytest.raises(RepositoryCloneError):
        collector.collect(ADDRESS, tmp_path / "m")


def test_metadata_without_a_valid_version_is_not_found(settings, tmp_path):
    collector = make_collector(settings, metadata_handler(version="not-semver"))
    with pytest.raises(RegistryModuleNotFoundError):
        collector.collect(ADDRESS, tmp_path / "m")
