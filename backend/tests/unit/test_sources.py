import pytest

from app.collectors.github import GitHubCollector
from app.collectors.router import default_router
from app.collectors.source_detector import detect_source, normalize_github_url
from app.collectors.terraform_registry import TerraformRegistryCollector
from app.core.exceptions import UnsupportedSourceError
from app.models.enums import SourceType


@pytest.mark.parametrize(
    "source",
    [
        "https://github.com/Org/Repo",
        "https://github.com/org/repo.git",
        "https://github.com/org/repo/",
        " https://github.com/ORG/repo.git/ ",
    ],
)
def test_normalization(source):
    assert normalize_github_url(source) == "https://github.com/org/repo"


@pytest.mark.parametrize(
    "source",
    [
        "file:///etc/passwd",
        "git@github.com:org/repo",
        "ssh://github.com/org/repo",
        "http://github.com/org/repo",
        "https://evil.com/org/repo",
        "https://github.com.evil.com/org/repo",
        "https://user:password@github.com/org/repo",
        "https://github.com:443/org/repo",
        "https://github.com/org/..",
        "https://github.com/org/.git",
        "https://github.com/org/repo?ref=main",
        "https://github.com/org/repo#x",
        "https://github.com/org/repo/tree/main",
        "https://github.com/org/repo;touch",
        "https://github.com/org/%2e%2e",
        "https://github.com/org/re\npo",
        "https://github.com/org/repo?",
        "https://github.com/org/-bad",
        "--upload-pack=evil",
    ],
)
def test_invalid_url(source):
    with pytest.raises(UnsupportedSourceError):
        detect_source(source)


def test_detect_and_collector_support(settings):
    github = detect_source("https://github.com/Org/Repo.git")
    assert github.source_type == SourceType.GITHUB
    assert github.owner == "org"
    assert GitHubCollector(settings).supports(github.source_url)
    assert not GitHubCollector(settings).supports("file:///tmp/x")
    registry = detect_source("terraform-aws-modules/vpc/aws")
    assert registry.source_type == SourceType.TERRAFORM_REGISTRY
    assert registry.provider == "aws"
    collector = TerraformRegistryCollector(settings)
    assert collector.supports(registry.source_url)
    assert not collector.supports("https://evil.com/a/b")
    assert not collector.supports("https://github.com/org/repo")


def test_router_picks_the_matching_collector(settings):
    router = default_router(settings)
    assert router.supports("https://github.com/org/repo")
    assert router.supports("terraform-aws-modules/vpc/aws")
    assert not router.supports("file:///tmp/x")
    github, registry = router.collectors
    assert isinstance(github, GitHubCollector)
    assert isinstance(registry, TerraformRegistryCollector)
    # The Registry collector reuses the single GitHub clone path, not a second one.
    assert registry.github is github
