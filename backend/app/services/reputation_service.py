"""Public, allowlisted evidence only. No credentials, redirects, or popularity score."""

import re
from datetime import UTC, datetime
from urllib.parse import urlsplit

import httpx
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from app.models.reputation import ReputationSnapshot
from app.schemas.reputation import ReputationEvaluation, ReputationMetric

POLICY = "evidence-v1"


def github_repo(source):
    url = urlsplit(source or "")
    if url.scheme != "https" or url.hostname != "github.com" or url.username or url.port:
        return None
    match = re.fullmatch(r"/([A-Za-z0-9_.-]+)/([A-Za-z0-9_.-]+?)(?:\.git)?/?", url.path)
    return "/".join(match.groups()) if match else None


def registry_module(source):
    url = urlsplit(source or "")
    if url.scheme != "https" or url.hostname != "registry.terraform.io" or url.username or url.port:
        return None
    match = re.fullmatch(
        r"/modules/([A-Za-z0-9_-]+/[A-Za-z0-9_-]+/[A-Za-z0-9_-]+)(?:/([A-Za-z0-9_.-]+))?/?",
        url.path,
    )
    return match.group(1) if match else None


def fetch_json(url):
    # Fixed hosts and constructed paths only; a remote redirect must not become an SSRF.
    with httpx.Client(timeout=8, follow_redirects=False, trust_env=False) as client:
        for attempt in range(2):
            try:
                with client.stream(
                    "GET",
                    url,
                    headers={"Accept": "application/json", "User-Agent": "ModuleGuard-evidence/1"},
                ) as response:
                    if response.status_code in (403, 429):
                        return None, "RATE_LIMITED"
                    if response.status_code == 404:
                        return None, "NOT_FOUND"
                    if response.status_code >= 500 and attempt == 0:
                        continue
                    if response.status_code != 200:
                        return None, "SOURCE_UNAVAILABLE"
                    data = bytearray()
                    for chunk in response.iter_bytes():
                        data.extend(chunk)
                        if len(data) > 524288:
                            return None, "RESPONSE_LIMIT"
                    import json

                    body = json.loads(data)
                    return (body, None) if isinstance(body, dict) else (None, "INVALID_RESPONSE")
            except (httpx.HTTPError, ValueError):
                if attempt:
                    return None, "SOURCE_UNAVAILABLE"
    return None, "SOURCE_UNAVAILABLE"


def evaluate(
    version_id,
    manifest,
    source,
    commit,
    at,
    github=None,
    github_error=None,
    registry=None,
    registry_error=None,
):
    """Deterministic: all time and observations are explicit inputs."""
    metrics = []
    files = {f["path"].lower() for f in (manifest or {}).get("files", [])}

    def metric(id, axis, value, formula, url=None, status=None, reason=None):
        metrics.append(
            ReputationMetric(
                id=id,
                axis=axis,
                value=value,
                formula=formula,
                source_url=url,
                status=status or ("confirmed" if value is not None else "unverified"),
                reason=reason,
            )
        )

    repo = github_repo(source)
    github_url = f"https://api.github.com/repos/{repo}" if repo else None
    # Presence is evidence of files, never evidence of test success or operational practice.
    for key, axis, present in [
        (
            "security_policy_file",
            "security",
            any(f in files for f in ("security.md", ".github/security.md")),
        ),
        (
            "readme_file",
            "documentation",
            any(f.rsplit("/", 1)[-1].startswith("readme") for f in files),
        ),
        (
            "test_files",
            "tests",
            any(f.startswith("tests/") or f.endswith(".tftest.hcl") for f in files),
        ),
    ]:
        metric(
            key,
            axis,
            present if manifest else None,
            "수집 manifest의 파일 존재 여부; 실행·품질은 확인하지 않음",
            status="confirmed" if manifest else "insufficient",
        )
    metric("commit", "provenance", commit, "수집 시 기록한 원본 commit", source if repo else None)
    for key, axis in [
        ("archived", "maintenance"),
        ("pushed_at", "maintenance"),
        ("created_at", "maintenance"),
        ("stargazers_count", "popularity"),
    ]:
        value = github.get(key) if github else None
        if not isinstance(value, (str, int, bool)):
            value = None
        metric(
            key,
            axis,
            value,
            "GitHub 공개 저장소 응답의 해당 필드; 활동 부족을 관리 중단으로 단정하지 않음",
            github_url,
            status="confirmed"
            if value is not None
            else "failed"
            if github_error
            else "not_applicable"
            if not repo
            else "unverified",
            reason=github_error,
        )
    registry_id = registry_module(source)
    for key in ("published_at", "downloads"):
        value = registry.get(key) if registry else None
        if not isinstance(value, (str, int)):
            value = None
        metric(
            f"registry_{key}",
            "popularity" if key == "downloads" else "provenance",
            value,
            "Registry 공식 API 원자료; 다운로드는 안전성 지표가 아님",
            f"https://registry.terraform.io/v1/modules/{registry_id}" if registry_id else None,
            status="confirmed"
            if value is not None
            else "failed"
            if registry_error
            else "not_applicable"
            if not registry_id
            else "unverified",
            reason=registry_error,
        )
    coverage = {}
    for axis in ("security", "maintenance", "provenance", "documentation", "tests"):
        applicable = [m for m in metrics if m.axis == axis and m.status != "not_applicable"]
        coverage[axis] = (
            round(sum(m.status == "confirmed" for m in applicable) / len(applicable) * 100, 2)
            if applicable
            else 0
        )
    raw = {
        "github": {
            k: github[k]
            for k in ("archived", "pushed_at", "created_at", "stargazers_count")
            if github and k in github
        },
        "registry": {
            k: registry[k] for k in ("published_at", "downloads") if registry and k in registry
        },
    }
    return ReputationEvaluation(
        version_id=version_id, collected_at=at, metrics=metrics, coverage=coverage, raw=raw
    )


def collect(session, version):
    now = datetime.now(UTC)
    bucket = now.strftime("%Y-%m-%dT%H")
    query = select(ReputationSnapshot).where(
        ReputationSnapshot.version_id == version.id, ReputationSnapshot.bucket == bucket
    )
    cached = session.scalar(query)
    if cached:
        return ReputationEvaluation.model_validate(cached.payload)
    source = version.source_url
    repo, registry_id = github_repo(source), registry_module(source)
    github, ge = fetch_json(f"https://api.github.com/repos/{repo}") if repo else (None, None)
    if github and github.get("private") is not False:
        github, ge = None, "NOT_PUBLIC"
    registry, re = (
        fetch_json(f"https://registry.terraform.io/v1/modules/{registry_id}")
        if registry_id
        else (None, None)
    )
    result = evaluate(
        version.id, version.manifest, source, version.commit_hash, now, github, ge, registry, re
    )
    session.add(
        ReputationSnapshot(
            version_id=version.id,
            collected_at=now,
            bucket=bucket,
            policy_version=POLICY,
            payload=result.model_dump(mode="json"),
        )
    )
    try:
        session.commit()
    except IntegrityError:
        session.rollback()
        existing = session.scalar(query)
        if existing is None:
            raise
        return ReputationEvaluation.model_validate(existing.payload)
    return result
