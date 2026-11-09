from datetime import UTC, datetime
from uuid import uuid4

from app.services.reputation_service import evaluate, github_repo, registry_module


def test_deterministic_missing_popularity_and_file_evidence():
    args = (
        uuid4(),
        {"files": [{"path": "README.md"}]},
        "https://github.com/org/repo",
        "a" * 40,
        datetime(2026, 1, 1, tzinfo=UTC),
    )
    a = evaluate(*args, github={"archived": False, "stargazers_count": 0})
    b = evaluate(*args, github={"archived": False, "stargazers_count": 10000000})
    assert a.coverage == b.coverage
    assert a.total_score is None and b.total_score is None
    assert a == evaluate(*args, github={"archived": False, "stargazers_count": 0})
    missing = evaluate(*args, github_error="RATE_LIMITED")
    assert any(m.status == "failed" and m.value is None for m in missing.metrics)
    assert next(m for m in a.metrics if m.id == "test_files").value is False
    assert next(m for m in a.metrics if m.id == "pushed_at").value is None


def test_sources_reject_non_public_hosts_and_paths():
    assert github_repo("https://github.com/a/b.git") == "a/b"
    assert registry_module("https://registry.terraform.io/modules/a/b/aws/1.0.0") == "a/b/aws"
    for url in [
        "http://github.com/a/b",
        "https://github.com.evil/a/b",
        "https://user@github.com/a/b",
        "https://github.com/a/../../secret",
        "https://localhost/a/b",
    ]:
        assert github_repo(url) is None
