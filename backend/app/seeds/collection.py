"""Bulk collection: run a pinned candidate manifest through MirrorSeeder, one module at a time.

Nothing here fetches, filters, scans or stores anything itself. Every module goes through
`MirrorSeeder._one` (PublishService.prepare_github -> register -> ScanService.run), so the file
policy, secret rules, limits and the license allowlist are the ones every upload gets. This layer
adds what a long unattended run needs: a request rate limit, retry with backoff for transient
failures, a checkpoint to resume from, a reason for every module that was left out, and a
quality report written to files (no table, no migration).

Concurrency: PublishService holds one prepared source per owner, so a second prepare for the
same account would delete the first one's slot. A run that stores (`apply`) therefore handles
one module at a time. A preview stores nothing and uses one staging owner per module, so it may
run several modules at once.
"""

import json
import os
import random
import re
import threading
import time
from collections import Counter
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Module, ModuleVersion, Repository, Scan, User
from app.seeds.mirrors import (
    MirrorManifest,
    MirrorModule,
    MirrorSeeder,
    Outcome,
    SeedAborted,
)
from app.utils.subprocess import ProcessRunner, isolated_environment

STORED = ("added", "updated", "unchanged")
# Failures that say nothing about the module itself: the network, a timeout, the host.
TRANSIENT = ("RepositoryCloneError", "ScanTimeoutError", "TimeoutError", "ConnectionError")
# reason prefix -> category. Order matters: the first match wins.
CATEGORIES = (
    ("license:", "license"),
    ("SecretsDetected", "secret"),
    ("SensitiveFile", "sensitive_file"),
    ("NoTerraformFiles", "not_terraform"),
    ("PinnedCommitMismatch", "pin_mismatch"),
    ("UncompressedTooLarge", "too_large"),
    ("TooManyFiles", "too_large"),
    ("RepositoryTooLargeError", "too_large"),
    ("RepositoryCloneError", "clone_failed"),
    ("UnsupportedSourceError", "unsupported_source"),
)
VERSION_TAG = re.compile(r"refs/tags/(v?[0-9]{1,5}\.[0-9]{1,5}\.[0-9]{1,5})(\^\{\})?")
GIT_SAFE = [
    "git",
    "-c",
    "http.followRedirects=false",
    "-c",
    "protocol.allow=never",
    "-c",
    "protocol.https.allow=always",
]


# ------------------------------------------------------------------ manifest checks and pins


def check_manifest(
    manifest: MirrorManifest, already: MirrorManifest | None = None
) -> tuple[list[MirrorModule], dict[str, str]]:
    """Modules worth running, and the reason for each one that is not.

    A repeated repository or name inside the manifest is a mistake in the manifest and stops
    the run: two repositories with one derived name would be published into one repository.
    Entries that repeat a commit already listed, or a repository of `already` (the 15 mirrors),
    are skipped and reported as duplicates.
    """
    repositories = [m.repository.lower() for m in manifest.modules]
    names = [m.name for m in manifest.modules]
    if len(set(repositories)) != len(repositories) or len(set(names)) != len(names):
        raise SeedAborted("The manifest lists the same repository or module name twice.")
    mirrored = {m.repository.lower() for m in already.modules} if already else set()
    kept, skipped, commits = [], {}, {}
    for module in manifest.modules:
        if module.repository.lower() in mirrored:
            skipped[module.repository] = "already in the mirror manifest"
        elif module.commit in commits:
            skipped[module.repository] = f"same commit as {commits[module.commit]}"
        else:
            commits[module.commit] = module.repository
            kept.append(module)
    return kept, skipped


def _ls_remote(runner: ProcessRunner, repository: str, patterns: list[str], work: Path) -> str:
    result = runner.run(
        [*GIT_SAFE, "ls-remote", "--", f"https://github.com/{repository}", *patterns],
        cwd=work,
        timeout=60,
        env=isolated_environment(work),
        output_limit=4 * 1024**2,
    )
    if result.returncode != 0:
        raise RuntimeError(f"git ls-remote failed for {repository}")  # stderr is never shown
    return result.stdout


def resolve_pin(runner: ProcessRunner, repository: str, tag: str, work: Path) -> str:
    """The commit a tag points at. An annotated tag lists the tag object first and the commit
    on the peeled `^{}` line, which is the one MirrorSeeder compares against."""
    lines = _ls_remote(runner, repository, [f"refs/tags/{tag}", f"refs/tags/{tag}^{{}}"], work)
    found: dict[str, str] = {}
    for line in lines.splitlines():
        sha, _, ref = line.partition("\t")
        found[ref] = sha
    commit = found.get(f"refs/tags/{tag}^{{}}") or found.get(f"refs/tags/{tag}")
    if commit is None:
        raise RuntimeError(f"tag {tag} not found in {repository}")
    return commit


def latest_tag(runner: ProcessRunner, repository: str, work: Path) -> str:
    """Highest x.y.z release tag; pre-releases like v1.0.0-rc1 never match the pattern."""
    tags = set()
    for line in _ls_remote(runner, repository, ["refs/tags/*"], work).splitlines():
        match = VERSION_TAG.fullmatch(line.partition("\t")[2])
        if match:
            tags.add(match.group(1))
    if not tags:
        raise RuntimeError(f"no release tag in {repository}")
    return max(tags, key=lambda t: tuple(int(p) for p in t.lstrip("v").split(".")))


# ------------------------------------------------------------------ one module, with retries


@dataclass
class Record:
    """What happened to one candidate. This is a line of the checkpoint and of the report."""

    repository: str
    name: str
    tag: str
    commit: str
    acknowledged: bool
    # stored | stored_scan_failed | quarantined | would_add | duplicate
    state: str
    category: str
    action: str = ""
    reason: str = ""
    license: str | None = None
    files: int | None = None
    excluded: int = 0
    secret_warnings: int = 0
    provider: str | None = None
    scan_status: str | None = None
    partial: bool = False
    failure_stage: str | None = None
    attempts: int = 1

    @property
    def key(self) -> str:
        return f"{self.repository}@{self.commit}:{int(self.acknowledged)}"


def categorize(reason: str) -> str:
    for prefix, category in CATEGORIES:
        if reason.startswith(prefix):
            return category
    if "possible secret(s) need review" in reason:
        return "secret_review"
    if reason.startswith("scan "):
        return "scan_failed"
    return "other"


def is_transient(reason: str) -> bool:
    return reason.startswith(TRANSIENT)


def _scan_details(session: Session, login: str, module: MirrorModule, record: Record) -> None:
    """Fill the stored version's license, size, provider and scan result from the database."""
    user = session.scalar(select(User).where(func.lower(User.login) == login.lower()))
    repository = (
        session.scalar(
            select(Repository).where(Repository.owner_id == user.id, Repository.name == module.name)
        )
        if user
        else None
    )
    version = None
    if repository is not None:
        record.provider = repository.provider
        version = session.scalar(
            select(ModuleVersion).where(
                ModuleVersion.repository_id == repository.id,
                ModuleVersion.commit_hash == module.commit,
                ModuleVersion.snapshot_sha256.is_not(None),
            )
        )
    scan = session.get(Scan, version.scan_id) if version and version.scan_id else None
    if version is not None:
        record.license, record.files = version.license, version.file_count
    if scan is None:  # A scan that failed before it stored anything is looked up by module.
        scan = session.scalar(
            select(Scan)
            .join(Module, Scan.module_id == Module.id)
            .where(func.lower(Module.source_url) == module.url.lower())
            .where(Scan.commit_hash == module.commit)
            .order_by(Scan.created_at.desc())
        )
    if scan is not None:
        record.scan_status = scan.status.value
        record.partial = bool(scan.partial_result)
        failure = (scan.analysis or {}).get("failure") or {}
        record.failure_stage = failure.get("stage")


class BatchCollector:
    def __init__(
        self,
        seeder: MirrorSeeder,
        *,
        retries: int = 3,
        backoff: float = 2.0,
        backoff_cap: float = 60.0,
        min_interval: float = 1.0,
        workers: int = 1,
        checkpoint: Path | None = None,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.monotonic,
        jitter: Callable[[], float] = random.random,
    ):
        self.seeder = seeder
        self.retries, self.backoff, self.backoff_cap = retries, backoff, backoff_cap
        self.min_interval, self.workers = min_interval, workers
        self.checkpoint = checkpoint
        self.sleep, self.clock, self.jitter = sleep, clock, jitter
        self.account = None  # set by run()
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._next_start = 0.0
        self._done: dict[str, Record] = {}

    # -- rate limit and backoff

    def _throttle(self) -> None:
        with self._lock:
            now = self.clock()
            wait = max(0.0, self._next_start - now)
            self._next_start = max(now, self._next_start) + self.min_interval
        if wait:
            self.sleep(wait)

    def _delay(self, attempt: int) -> float:
        # Exponential, capped, with jitter so parallel previews do not retry in step.
        return min(self.backoff_cap, self.backoff * 2**attempt) * (0.5 + self.jitter() / 2)

    # -- checkpoint

    def load(self, apply: bool) -> None:
        """Resume: modules that reached a final state in an earlier run of the same mode."""
        if self.checkpoint is None:
            return
        try:
            data = json.loads(self.checkpoint.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if data.get("apply") != apply:
            return
        for item in data.get("records", []):
            record = Record(**item)
            self._done[record.key] = record

    def _save(self, apply: bool) -> None:
        if self.checkpoint is None:
            return
        payload = {"apply": apply, "records": [asdict(r) for r in self._done.values()]}
        self.checkpoint.parent.mkdir(parents=True, exist_ok=True)
        temp = self.checkpoint.with_name(self.checkpoint.name + ".tmp")
        temp.write_text(json.dumps(payload, ensure_ascii=False, indent=1), encoding="utf-8")
        os.replace(temp, self.checkpoint)  # a half-written checkpoint is never visible

    # -- one module

    def _attempt(self, module: MirrorModule, apply: bool) -> tuple[Outcome, int]:
        manifest = MirrorManifest(account=self.account, modules=[module])
        for attempt in range(self.retries + 1):
            self._throttle()
            try:
                (outcome,) = self.seeder.run(manifest, apply)
            except SeedAborted:
                raise
            except Exception as error:  # noqa: BLE001 - one module never stops the batch
                # Only the type is kept: messages of external tools may carry untrusted text.
                outcome = Outcome(module.name, module.tag, "failed", type(error).__name__)
            reason = outcome.reason
            if outcome.action in ("skipped", "failed") and is_transient(reason):
                if attempt < self.retries:
                    self.sleep(self._delay(attempt))
                    continue
            return outcome, attempt + 1
        raise AssertionError("unreachable")

    def _record(self, module: MirrorModule, outcome: Outcome, attempts: int, apply: bool) -> Record:
        record = Record(
            module.repository,
            module.name,
            module.tag,
            module.commit,
            module.acknowledge_warnings,
            "quarantined",
            categorize(outcome.reason),
            outcome.action,
            outcome.reason,
            outcome.license,
            outcome.files,
            outcome.excluded,
            outcome.warning_count,
            attempts=attempts,
        )
        if outcome.action in STORED or (outcome.action == "failed" and apply):
            with self.seeder.session_factory() as session:
                _scan_details(session, self.account.login, module, record)
        if outcome.action in STORED:
            record.state, record.category = "stored", "ok"
            if record.scan_status == "FAILED":
                # The snapshot is stored but the scan failed: it is not counted as a success.
                record.state = "stored_scan_failed"
                record.category = (
                    "parser_failed" if record.failure_stage == "parse" else "scan_failed"
                )
                record.reason = (
                    record.reason or f"scan failed at {record.failure_stage or 'unknown'}"
                )
        elif outcome.action in ("add", "update"):
            record.state, record.category = "would_add", "ok"
        elif outcome.action == "failed" and record.failure_stage == "parse":
            record.category = "parser_failed"
        return record

    def _one(self, module: MirrorModule, apply: bool) -> Record:
        key = f"{module.repository}@{module.commit}:{int(module.acknowledge_warnings)}"
        previous = self._done.get(key)
        if previous is not None:
            return previous
        outcome, attempts = self._attempt(module, apply)
        record = self._record(module, outcome, attempts, apply)
        # A failure that may pass on its own is retried by the next run, not remembered.
        if not is_transient(record.reason):
            with self._lock:
                self._done[record.key] = record
                self._save(apply)
        return record

    def run(
        self,
        manifest: MirrorManifest,
        apply: bool,
        already: MirrorManifest | None = None,
        on_record: Callable[[Record], None] | None = None,
    ) -> list[Record]:
        modules, skipped = check_manifest(manifest, already)
        self.account = manifest.account
        self.load(apply)
        workers = 1 if apply else max(1, self.workers)
        records: list[Record] = []
        for module in manifest.modules:
            if module.repository in skipped:
                records.append(
                    Record(
                        module.repository, module.name, module.tag, module.commit,
                        module.acknowledge_warnings, "duplicate", "duplicate",
                        reason=skipped[module.repository],
                    )
                )  # fmt: skip
        self._stop.clear()

        def task(module: MirrorModule) -> Record | None:
            if self._stop.is_set():
                return None  # An earlier module was interrupted: do not start this one.
            try:
                return self._one(module, apply)
            except BaseException:
                self._stop.set()
                raise

        pool = ThreadPoolExecutor(workers)
        futures = [pool.submit(task, module) for module in modules]
        try:
            for future in futures:
                record = future.result()
                if record is None:
                    continue
                records.append(record)
                if on_record:
                    on_record(record)
        except BaseException:
            # Ctrl+C or an abort: the module being processed finishes, queued ones never start.
            self._stop.set()
            pool.shutdown(wait=True, cancel_futures=True)
            with self._lock:
                self._save(apply)  # What finished is kept; run again with resume.
            raise
        pool.shutdown()
        return records


# ------------------------------------------------------------------ quality metrics


def _rate(part: int, whole: int) -> float | None:
    return round(part / whole, 3) if whole else None


def summarize(records: list[Record], candidates: int) -> dict:
    """Quality metrics. `success_rate` counts only modules stored AND scanned to COMPLETED."""
    eligible = [r for r in records if r.state != "duplicate"]
    stored = [r for r in eligible if r.state in ("stored", "stored_scan_failed")]
    ok = [r for r in stored if r.scan_status == "COMPLETED"]
    partial = [r for r in ok if r.partial]
    failed = [r for r in stored if r.scan_status == "FAILED"]
    prepared = [r for r in eligible if r.files is not None]  # reached the license/secret checks
    quarantined = [r for r in eligible if r.state == "quarantined"]
    providers = Counter(r.provider for r in stored if r.provider)
    return {
        "candidates": candidates,
        "duplicates": len(records) - len(eligible),
        "processed": len(eligible),
        "stored": len(stored),
        "would_add": sum(r.state == "would_add" for r in eligible),
        "quarantined": len(quarantined),
        "stored_scan_failed": len(failed),
        "by_category": dict(
            sorted(Counter(r.category for r in eligible if r.category != "ok").items())
        ),
        "success_rate": _rate(len(ok), len(eligible)),
        "collection_rate": _rate(len(stored), len(eligible)),
        "license_recognized_rate": _rate(
            sum(r.license is not None for r in prepared), len(prepared)
        ),
        "scan": {
            "completed": len(ok) - len(partial),
            "partial": len(partial),
            "failed": len(failed),
            "analysis_incomplete_rate": _rate(len(partial) + len(failed), len(stored)),
        },
        "files": {
            "total": sum(r.files or 0 for r in prepared),
            "excluded": sum(r.excluded for r in prepared),
        },
        "secret_warnings": sum(r.secret_warnings for r in prepared),
        "providers": {
            "coverage": _rate(sum(providers.values()), len(stored)),
            "by_name": dict(sorted(providers.items())),
        },
        "retried": sum(r.attempts > 1 for r in eligible),
    }


def write_reports(
    directory: Path, run_id: str, records: list[Record], summary: dict, apply: bool
) -> tuple[Path, Path]:
    """`<run_id>.json` for machines and `<run_id>.md` for people. Locations and rule names only:
    no secret value is ever part of a Record."""
    directory.mkdir(parents=True, exist_ok=True)
    mode = "apply" if apply else "preview"
    body = {
        "run_id": run_id,
        "mode": mode,
        "summary": summary,
        "records": [asdict(r) for r in records],
    }
    json_path, md_path = directory / f"{run_id}.json", directory / f"{run_id}.md"
    json_path.write_text(json.dumps(body, ensure_ascii=False, indent=1), encoding="utf-8")
    md_path.write_text(_markdown(run_id, mode, records, summary), encoding="utf-8")
    return json_path, md_path


def _pct(value: float | None) -> str:
    return "-" if value is None else f"{value:.1%}"


def _markdown(run_id: str, mode: str, records: list[Record], s: dict) -> str:
    lines = [
        f"# 수집 리포트 {run_id} ({mode})",
        "",
        f"- 후보 {s['candidates']}개, 중복 제외 {s['duplicates']}개, 처리 {s['processed']}개",
        f"- 저장 {s['stored']}개 (이 중 스캔 실패 {s['stored_scan_failed']}개), 격리 {s['quarantined']}개"
        f", 저장 예정(preview) {s['would_add']}개",
        f"- 성공률(저장 + 스캔 COMPLETED) {_pct(s['success_rate'])}, 수집률 {_pct(s['collection_rate'])}",
        f"- 인식된 라이선스 비율 {_pct(s['license_recognized_rate'])}",
        f"- 스캔: COMPLETED {s['scan']['completed']}, PARTIAL {s['scan']['partial']}, "
        f"FAILED {s['scan']['failed']} (분석 불완전 {_pct(s['scan']['analysis_incomplete_rate'])})",
        f"- 파일 {s['files']['total']}개, 정책으로 제외된 파일 {s['files']['excluded']}개, "
        f"비밀값 경고 {s['secret_warnings']}건",
        f"- provider 커버리지 {_pct(s['providers']['coverage'])}: {s['providers']['by_name']}",
        f"- 일시 오류로 재시도한 모듈 {s['retried']}개",
        "",
        "## 격리·제외 사유별 건수",
        "",
    ]
    lines += [f"- {name}: {count}" for name, count in s["by_category"].items()] or ["- 없음"]
    lines += [
        "",
        "## 모듈별 결과",
        "",
        "| 모듈 | 태그 | 상태 | 사유 분류 | 라이선스 | 파일 | 사유 |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in records:
        reason = r.reason.replace("|", "/").replace("\n", " ")[:160]
        lines.append(
            f"| {r.repository} | {r.tag} | {r.state} | {r.category} | {r.license or '-'} "
            f"| {r.files if r.files is not None else '-'} | {reason} |"
        )
    return "\n".join(lines) + "\n"


def run_id() -> str:
    return datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
