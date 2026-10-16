import pytest

from app.analyzers.classifiers import (
    GitRefClassifier,
    SourceClassifier,
    VersionConstraintClassifier,
    is_official_provider,
)


@pytest.mark.parametrize(
    "source,kind",
    [
        ("terraform-aws-modules/vpc/aws", "REGISTRY"),
        ("registry.terraform.io/terraform-aws-modules/vpc/aws", "REGISTRY"),
        ("registry.example.com/team/network/aws//modules/vpc", "REGISTRY"),
        ("github.com/org/repo", "GITHUB"),
        ("https://github.com/org/repo", "GITHUB"),
        ("git::https://github.com/org/repo.git?ref=main", "GIT"),
        ("git::ssh://git@gitlab.com/org/repo.git", "GIT"),
        ("git@github.com:org/repo.git", "GIT"),
        ("./modules/vpc", "LOCAL"),
        ("../shared", "LOCAL"),
        ("https://example.com/module.zip", "HTTP"),
        ("${var.source}", "UNKNOWN"),
        (None, "UNKNOWN"),
        ("nonsense", "UNKNOWN"),
    ],
)
def test_source_classification(source, kind):
    assert SourceClassifier().classify(source).source_type == kind


@pytest.mark.parametrize(
    "ref,kind",
    [
        (None, "MISSING"),
        ("", "MISSING"),
        ("main", "BRANCH"),
        ("develop", "BRANCH"),
        ("refs/heads/custom", "BRANCH"),
        ("feature/new", "BRANCH"),
        ("v1.2.3", "TAG"),
        ("1.2.3-beta.1", "TAG"),
        ("refs/tags/release", "TAG"),
        ("a" * 40, "COMMIT_SHA"),
        ("b" * 64, "COMMIT_SHA"),
        ("2f5cd89", "UNKNOWN"),
        ("production", "UNKNOWN"),
    ],
)
def test_git_ref(ref, kind):
    assert GitRefClassifier().classify(ref) == kind


def test_git_query_and_subdirectory():
    info = SourceClassifier().classify(
        "git::https://github.com/org/repo.git//modules/x?ref=refs%2Fheads%2Fmain&depth=1"
    )
    assert info.ref == "refs/heads/main" and info.ref_type == "BRANCH"
    assert not info.immutable
    assert SourceClassifier().classify("github.com/org/repo?ref=" + "a" * 40).immutable
    assert SourceClassifier().classify("github.com/org/repo?ref=a&ref=b").ref_type == "UNKNOWN"


@pytest.mark.parametrize(
    "version,kind",
    [
        (None, "MISSING"),
        ("", "MISSING"),
        ("5.1.0", "EXACT"),
        ("= 5.1.0", "EXACT"),
        ("~> 5.0", "BOUNDED"),
        (">= 1.0, < 2.0", "BOUNDED"),
        ("< 4.0", "BOUNDED"),
        (">= 1.0", "BROAD"),
        ("!= 1.0", "BROAD"),
        ("*", "UNBOUNDED"),
        (">= 0.0.0", "UNBOUNDED"),
        ("${var.version}", "UNKNOWN"),
        ("latest", "UNKNOWN"),
        ("~>", "UNKNOWN"),
        ("1.0 || 2.0", "UNKNOWN"),
    ],
)
def test_version_constraints(version, kind):
    assert VersionConstraintClassifier().classify(version) == kind


@pytest.mark.parametrize(
    "source,expected",
    [
        ("hashicorp/aws", True),
        ("registry.terraform.io/hashicorp/aws", True),
        ("example.com/hashicorp/aws", False),
        ("community/aws", False),
        ("hashicorp/aws/extra", False),
        (None, False),
        ("${var.provider}", False),
    ],
)
def test_official_provider(source, expected):
    assert is_official_provider(source) is expected
