import logging
import secrets
import shutil
import time
from collections import Counter
from collections.abc import Callable
from uuid import UUID

from sqlalchemy.orm import Session, sessionmaker

from app.analyzers.data_flow import DataFlowAnalyzer
from app.analyzers.module_interface import (
    detect_license,
    extract_interface,
    extract_provider,
    read_readme,
)
from app.collectors.base import CollectedModule, SourceCollector
from app.collectors.router import default_router
from app.collectors.source_detector import detect_source
from app.core.config import Settings
from app.core.exceptions import (
    ConflictError,
    DomainError,
    NotFoundError,
    PreparedSourceMissingError,
    RateLimitedError,
    ScanQueueFullError,
    SnapshotError,
    TerraformNotFoundError,
    UploadRejectedError,
)
from app.correlation.context import CorrelationContext
from app.correlation.engine import CorrelationEngine
from app.graphs.builder import DependencyGraphBuilder
from app.graphs.flow_builder import DataFlowGraphBuilder
from app.models import DownloadArtifact, ModuleVersion, Repository, User
from app.models.common import utcnow
from app.models.enums import ScanStatus
from app.parsers.hcl import HCLTerraformParser
from app.repositories.finding_repository import FindingRepository
from app.repositories.graph_repository import GraphRepository
from app.repositories.module_repository import ModuleRepository
from app.repositories.scan_repository import ScanRepository
from app.repositories.version_repository import VersionRepository
from app.scanners.base import SecurityScanner
from app.scanners.checkov import CheckovScanner
from app.scanners.normalizer import (
    CheckovNormalizer,
    FindingNormalizer,
    SeverityMapper,
    summarize,
)
from app.scanners.provenance import capture_provenance
from app.scanners.scope import MODULE, finding_scope, scope_summary
from app.scanners.severity_overrides import SEVERITY_OVERRIDES
from app.schemas.analysis import AnalysisStage, AnalysisSummary, StageStatus
from app.schemas.scan import ScanAccepted
from app.services.analysis_service import StaticAnalysisResult, StaticAnalysisService
from app.storage.artifacts import wrapper
from app.storage.integrity import IntegrityError, compare, digest, verify_tree
from app.storage.snapshots import SnapshotStore
from app.uploads.staging import StagingStore
from app.usage.archive import build_download
from app.usage.hcl import module_label
from app.utils.filesystem import check_repository_size, terraform_files
from app.utils.terraform import validate_local_module_paths
from app.workspace.manager import WorkspaceManager

logger = logging.getLogger(__name__)

# Pipeline stages in execution order. A FAILED scan reports the furthest one it reached.
STAGE_ORDER = (
    "checkov",
    "parse",
    "graph",
    "source_trust",
    "data_flow",
    "correlation",
    "persistence",
)


class ScanService:
    """Durable scan records; collector/scanner/normalizer can be independently replaced."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        collector: SourceCollector | None = None,
        scanner: SecurityScanner | None = None,
        normalizer: FindingNormalizer | None = None,
        analysis_service: StaticAnalysisService | None = None,
        correlation_engine: CorrelationEngine | None = None,
        snapshot_store: SnapshotStore | None = None,
        staging: StagingStore | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        # Identifies this process as the owner of the scans it runs; a new value per start, so
        # recovery can tell this worker's own runs from those left by a process that died.
        self.worker_id = secrets.token_hex(8)
        self.collector = collector or default_router(settings)
        self.scanner = scanner or CheckovScanner(settings)
        self.normalizer = normalizer or CheckovNormalizer(SeverityMapper(SEVERITY_OVERRIDES))
        self.correlation_engine = correlation_engine or CorrelationEngine()
        self.staging = staging or StagingStore(
            settings.upload_root, settings.upload_ttl_minutes * 60
        )
        self.snapshot_store = snapshot_store or SnapshotStore(
            settings.snapshot_root, settings.max_snapshot_size_mb * 1024**2
        )
        self.analysis_service = analysis_service or StaticAnalysisService(
            parser=HCLTerraformParser(
                max_depth=settings.max_local_module_depth,
                max_entities=settings.max_analysis_entities,
                max_file_bytes=settings.max_terraform_file_bytes,
            ),
            builder=DependencyGraphBuilder(max_edges=settings.max_analysis_edges),
            flow_analyzer=DataFlowAnalyzer(DataFlowGraphBuilder(settings.max_flow_edges)),
        )

    def create(self, source: str) -> ScanAccepted:
        identity = detect_source(source)
        with self.session_factory() as session:
            module = ModuleRepository(session).get_or_create(identity)
            scan = ScanRepository(session).create(module.id)
            scan.scanner = self.scanner.name
            session.commit()
            return ScanAccepted(scan_id=scan.id)

    @staticmethod
    def _failed_stage(analysis: dict | None) -> str:
        """The furthest stage the pipeline had reached, or "collect" before any stage began."""
        stages = (analysis or {}).get("stages") or {}
        for name in reversed(STAGE_ORDER):
            if stages.get(name, {}).get("status") in {StageStatus.RUNNING, StageStatus.FAILED}:
                return name
        return "collect"

    def mark_failed(
        self,
        scan_id: UUID,
        message: str,
        started: float | None = None,
        code: str | None = None,
    ) -> None:
        with self.session_factory() as session:
            scan = ScanRepository(session).get(scan_id)
            if scan.module_version is not None and code == "SnapshotError":
                scan.module_version.integrity_status = "ERROR"
            scan.status = ScanStatus.FAILED
            scan.error_message = message
            scan.finished_at = utcnow()
            # Resolve the failed stage from the stored stages before they are rewritten below.
            failure = {"stage": self._failed_stage(scan.analysis), "code": code} if code else None
            if scan.analysis:
                analysis = dict(scan.analysis)
                stages = {name: dict(stage) for name, stage in analysis.get("stages", {}).items()}
                for stage in stages.values():
                    if stage["status"] == StageStatus.RUNNING:
                        stage.update(
                            status=StageStatus.FAILED,
                            error_code="PipelineAborted",
                            message="Scan pipeline aborted.",
                        )
                    elif stage["status"] == StageStatus.PENDING:
                        stage["status"] = StageStatus.SKIPPED
                if stages.get("persistence", {}).get("status") == StageStatus.FAILED:
                    for key in (
                        "entities",
                        "edges",
                        "dependency_edges",
                        "modules",
                        "external_modules",
                        "providers",
                        "source_trust_findings",
                        "unresolved_references",
                    ):
                        analysis[key] = 0
                    analysis["data_flow"] = None
                    analysis["correlation"] = None
                scan.analysis = {**analysis, "stages": stages}
            if failure is not None:
                scan.analysis = {**(scan.analysis or {}), "failure": failure}
            if started is not None:
                scan.duration_ms = int((time.monotonic() - started) * 1000)
            session.commit()
        self.staging.release(scan_id)

    def _collect(self, scan, destination) -> CollectedModule:
        """Published scans analyse exactly the tree that was inspected, never a fresh download."""
        staged = self.staging.claimed(scan.id)
        if staged is None:
            if scan.module_version_id is not None:
                raise PreparedSourceMissingError("Prepared source is no longer available.")
            return self.collector.collect(scan.module.source_url, destination)
        tree, report = staged
        if (
            scan.module_version is None
            or scan.module_version.manifest is None
            or not scan.module_version.manifest_sha256
        ):
            raise IntegrityError("UNVERIFIED")
        verify_tree(
            tree,
            scan.module_version.manifest,
            self.snapshot_store.max_bytes,
            scan.module_version.manifest_sha256,
        )
        shutil.move(str(tree), str(destination))
        self.staging.release(scan.id)
        return CollectedModule(
            scan.module.source_type,
            scan.module.source_url,
            destination,
            scan.module.repository_name or scan.module.name,
            scan.module.repository_owner or "",
            report.get("branch"),
            report.get("commit_hash"),
        )

    def _snapshot(self, root) -> dict:
        """A scan without a stored source cannot back a version, so failure here fails the scan."""
        try:
            info = self.snapshot_store.save(root)
        except DomainError:
            raise
        except Exception as error:
            raise SnapshotError("Source snapshot could not be stored.") from error
        return {
            "manifest": info.manifest,
            "manifest_sha256": info.manifest_sha256,
            "snapshot_sha256": info.sha256,
            "size_bytes": info.size_bytes,
            "file_count": info.file_count,
        }

    def _describe(self, scan_id: UUID, root, static: StaticAnalysisResult) -> dict:
        """Interface, README and license. Descriptive only: failures never affect the scan."""
        described: dict = {}

        def attempt(action: str, work) -> None:
            try:
                described.update(work())
            except Exception as error:
                logger.error(
                    "scan_id=%s action=%s_failed error_type=%s",
                    scan_id,
                    action,
                    type(error).__name__,
                )

        def interface() -> dict:
            # A failed parse means the interface is unknown, not empty.
            if static.summary.stages["parse"].status not in {
                StageStatus.COMPLETED,
                StageStatus.PARTIAL,
            }:
                return {}
            variables, outputs = extract_interface(static.graph.nodes)
            return {"variables": variables, "outputs": outputs}

        attempt("interface", interface)
        attempt("provider", lambda: {"provider": extract_provider(static.graph.nodes)})
        attempt("readme", lambda: {"readme": read_readme(root), "license": detect_license(root)})
        return described

    def run(self, scan_id: UUID) -> None:
        started = time.monotonic()
        try:
            with self.session_factory() as session:
                scans = ScanRepository(session)
                if not scans.claim(scan_id):
                    return
                scan = scans.get(scan_id)
                # Liveness/ownership ride in the analysis JSONB (no new column): run() refreshes
                # them so recovery can tell a live run from one a dead process abandoned. The
                # retry link is carried through every analysis rewrite below so it is preserved.
                prior = scan.analysis or {}
                carry = {key: prior[key] for key in ("retry_of",) if key in prior}
                runtime = {
                    "owner": self.worker_id,
                    "attempt": (prior.get("runtime") or {}).get("attempt", 1),
                }

                def stamp(payload: dict) -> dict:
                    payload = dict(payload)
                    payload.update(carry)
                    payload["runtime"] = {**runtime, "heartbeat": time.time()}
                    return payload

                manager = WorkspaceManager(
                    self.settings.workspace_root, self.settings.keep_scan_workspace
                )
                with manager.create(scan_id) as workspace:
                    logger.info("scan_id=%s action=clone", scan_id)
                    collected = self._collect(scan, workspace / "module")
                    scan.commit_hash = collected.commit_hash
                    scan.branch = collected.branch
                    scan.module.default_branch = collected.branch
                    session.commit()
                    check_repository_size(
                        collected.local_path, self.settings.max_repository_size_mb * 1024**2
                    )
                    files = terraform_files(collected.local_path)
                    if not files:
                        raise TerraformNotFoundError("No Terraform files found in the repository.")
                    # Stored before analysis: without a snapshot there is nothing to publish.
                    artifacts = self._snapshot(collected.local_path)
                    if scan.module_version is not None:
                        compare(
                            scan.module_version.manifest,
                            artifacts["manifest"],
                            scan.module_version.manifest_sha256,
                        )
                        if scan.module_version.integrity_status == "MISMATCH":
                            raise IntegrityError()
                    artifacts.update(integrity_status="VERIFIED", verified_at=utcnow())
                    version = VersionRepository(session).record(
                        scan,
                        {
                            key: artifacts[key]
                            for key in (
                                "manifest",
                                "manifest_sha256",
                                "integrity_status",
                                "verified_at",
                            )
                        },
                    )
                    version.source_url = (
                        version.source_url or collected.repository_url or collected.source_url
                    )
                    version.source_ref = version.source_ref or collected.source_version
                    version.collected_at = version.collected_at or utcnow()
                    session.commit()
                    verify_tree(
                        collected.local_path,
                        artifacts["manifest"],
                        self.snapshot_store.max_bytes,
                        artifacts["manifest_sha256"],
                    )
                    provenance = capture_provenance(
                        self.settings, self.scanner, collected.local_path
                    )
                    scan.status = ScanStatus.SCANNING
                    checkov_stage = AnalysisStage(status=StageStatus.RUNNING)
                    scan.analysis = stamp(
                        {
                            **AnalysisSummary(stages={"checkov": checkov_stage}).model_dump(
                                mode="json"
                            ),
                            "provenance": provenance,
                        }
                    )
                    session.commit()
                    checkov_findings = []
                    checkov_warnings = []
                    checkov_error = None
                    logger.info("scan_id=%s action=checkov_start", scan_id)
                    try:
                        validate_local_module_paths(files, collected.local_path)
                        result = self.scanner.scan(collected.local_path)
                        scan.raw_result = result.raw_data
                        scan.status = ScanStatus.NORMALIZING
                        session.commit()
                        normalized = self.normalizer.normalize(result, collected.local_path)
                        checkov_findings = normalized.findings
                        checkov_warnings = normalized.warnings
                        checkov_stage = AnalysisStage(
                            status=StageStatus.PARTIAL
                            if normalized.partial
                            else StageStatus.COMPLETED
                        )
                        logger.info(
                            "scan_id=%s action=checkov_complete findings=%s",
                            scan_id,
                            len(checkov_findings),
                        )
                    except Exception as error:
                        checkov_error = (
                            str(error)
                            if isinstance(error, DomainError)
                            else "Internal Checkov analysis error."
                        )
                        checkov_stage = AnalysisStage(
                            status=StageStatus.FAILED,
                            error_code=type(error).__name__,
                            message=checkov_error,
                        )
                        logger.error(
                            "scan_id=%s action=checkov_failed error_type=%s",
                            scan_id,
                            type(error).__name__,
                        )
                    scan.status = ScanStatus.NORMALIZING

                    def publish(analysis: AnalysisSummary) -> None:
                        payload = analysis.model_dump(mode="json")
                        payload["stages"]["checkov"] = checkov_stage.model_dump(mode="json")
                        payload["provenance"] = provenance
                        scan.analysis = stamp(payload)
                        session.commit()

                    verify_tree(
                        collected.local_path,
                        artifacts["manifest"],
                        self.snapshot_store.max_bytes,
                        artifacts["manifest_sha256"],
                    )
                    static = self.analysis_service.run(collected.local_path, scan_id, publish)
                    verify_tree(
                        collected.local_path,
                        artifacts["manifest"],
                        self.snapshot_store.max_bytes,
                        artifacts["manifest_sha256"],
                    )
                    findings = checkov_findings + static.findings
                    if static.summary.stages["graph"].status in {
                        StageStatus.FAILED,
                        StageStatus.SKIPPED,
                    }:
                        static.summary.stages["correlation"] = AnalysisStage(
                            status=StageStatus.SKIPPED
                        )
                    else:
                        static.summary.stages["correlation"] = AnalysisStage(
                            status=StageStatus.RUNNING
                        )
                        publish(static.summary)
                        try:
                            correlated = self.correlation_engine.evaluate(
                                CorrelationContext(scan_id, static.graph, findings, self.settings)
                            )
                            findings.extend(correlated.findings)
                            static.summary.correlation = correlated.summary
                            static.summary.diagnostics.extend(correlated.diagnostics)
                            incomplete = (
                                bool(correlated.diagnostics)
                                or checkov_stage.status != StageStatus.COMPLETED
                                or any(
                                    s.status != StageStatus.COMPLETED
                                    for name, s in static.summary.stages.items()
                                    if name != "correlation"
                                )
                            )
                            static.summary.stages["correlation"] = AnalysisStage(
                                status=StageStatus.PARTIAL if incomplete else StageStatus.COMPLETED
                            )
                        except Exception as error:
                            static.summary.stages["correlation"] = AnalysisStage(
                                status=StageStatus.FAILED,
                                error_code=type(error).__name__,
                                message="Risk correlation could not complete.",
                            )
                            logger.error(
                                "scan_id=%s action=correlation_failed error_type=%s",
                                scan_id,
                                type(error).__name__,
                            )
                    static.summary.scanner_breakdown = dict(Counter(f.scanner for f in findings))
                    publish(static.summary)
                    for finding in findings:
                        finding.scope = finding_scope(finding.file_path, finding.raw_data)
                    # Counters describe the module body; examples and tests are kept apart.
                    summary = summarize([f for f in findings if f.scope == MODULE])
                    scan.scope_summary = scope_summary(findings)
                    scan.warnings = checkov_warnings + [
                        f"{name} analysis is {stage.status.value.lower()}; inspect analysis diagnostics."
                        for name, stage in static.summary.stages.items()
                        if stage.status
                        in {StageStatus.FAILED, StageStatus.PARTIAL, StageStatus.SKIPPED}
                    ]
                    scan.partial_result = bool(scan.warnings) or checkov_error is not None
                    scan.error_message = checkov_error
                    artifacts.update(self._describe(scan_id, collected.local_path, static))
                    verify_tree(
                        collected.local_path,
                        artifacts["manifest"],
                        self.snapshot_store.max_bytes,
                        artifacts["manifest_sha256"],
                    )
                # Cleanup, graph, findings and final summary are one published outcome.
                scan.analysis = {
                    **scan.analysis,
                    "stages": {
                        **scan.analysis["stages"],
                        "persistence": AnalysisStage(status=StageStatus.RUNNING).model_dump(
                            mode="json"
                        ),
                    },
                }
                session.commit()
                GraphRepository(session).save(scan_id, static.graph)
                if scan.module.source_url.startswith("fixture://"):
                    for finding in findings:
                        finding.raw_data = {**finding.raw_data, "development_fixture": True}
                FindingRepository(session).add_many(scan_id, findings)
                scan.analysis = {
                    **scan.analysis,
                    "stages": {
                        **scan.analysis["stages"],
                        "persistence": AnalysisStage(status=StageStatus.COMPLETED).model_dump(
                            mode="json"
                        ),
                    },
                }
                artifacts.pop("integrity_status", None)
                artifacts.pop("verified_at", None)
                VersionRepository(session).record(scan, artifacts)
                scan.total_findings = summary.total
                for severity in ("critical", "high", "medium", "low", "info", "unknown"):
                    setattr(scan, f"{severity}_count", getattr(summary, severity))
                scan.status = ScanStatus.FAILED if checkov_error else ScanStatus.COMPLETED
                if checkov_error:
                    # The only inline failure: analysis finished but Checkov itself did not.
                    scan.analysis = {
                        **scan.analysis,
                        "failure": {"stage": "checkov", "code": checkov_stage.error_code},
                    }
                if not checkov_error and version.repository is not None:
                    # Persist the default ZIP once. Custom wrappers receive their own artifact.
                    files, key = wrapper(version.repository.name, version.variables)
                    snapshot = self.snapshot_store.verified_bytes(
                        version.snapshot_sha256, version.manifest, version.manifest_sha256
                    )
                    with build_download(
                        snapshot, module_label(version.repository.name), files
                    ) as built:
                        content = built.read()
                    session.add(
                        DownloadArtifact(
                            version_id=version.id,
                            request_sha256=key,
                            zip_sha256=digest(content),
                            artifact_size=len(content),
                            content=content,
                        )
                    )
                scan.finished_at = utcnow()
                scan.duration_ms = int((time.monotonic() - started) * 1000)
                session.commit()
                logger.info("scan_id=%s action=scan_complete findings=%s", scan_id, summary.total)
        except Exception as error:
            # Do not persist or log arbitrary exception text from untrusted tools/content.
            message = (
                str(error)
                if isinstance(error, DomainError)
                else "Internal scan error. See server event logs."
            )
            logger.error("scan_id=%s action=failed error_type=%s", scan_id, type(error).__name__)
            self.mark_failed(scan_id, message, started, code=type(error).__name__)
            if isinstance(error, IntegrityError):
                with self.session_factory() as session:
                    version = ScanRepository(session).get(scan_id).module_version
                    if version is not None:
                        version.integrity_status = error.status
                        version.mismatched_files = [d["path"] for d in error.details]
                        session.commit()

    # ---------------------------------------------------------------- interrupted-scan recovery

    def recover_interrupted(self, submit: Callable[[UUID], None]) -> None:
        """Resolve scans a killed process left non-terminal. Safe at startup and concurrently.

        Queued scans are handed back to the dispatcher; running-stage scans are failed with a
        stable code. Each transition is an atomic conditional UPDATE (and the dispatcher's own
        claim), so concurrent recovery passes each take effect at most once.
        """
        try:
            with self.session_factory() as session:
                candidates = [
                    (scan.id, scan.status, scan.analysis)
                    for scan in ScanRepository(session).active()
                ]
        except Exception:
            logger.error("action=recovery_scan_failed")
            return
        stale_before = time.time() - self.settings.scan_recovery_stale_seconds
        for scan_id, status, analysis in candidates:
            if not self._recoverable(analysis, stale_before):
                continue
            try:
                if status == ScanStatus.QUEUED:
                    self._requeue(scan_id, submit)
                else:
                    self._fail_interrupted(scan_id)
            except Exception:
                logger.error("scan_id=%s action=recovery_failed", scan_id)

    def _recoverable(self, analysis: dict | None, stale_before: float) -> bool:
        """A single-worker deployment recovers every non-terminal scan; a multi-process one
        leaves scans whose heartbeat is still fresh to the worker that owns them."""
        if self.settings.scan_recovery_assume_single_worker:
            return True
        heartbeat = ((analysis or {}).get("runtime") or {}).get("heartbeat")
        return heartbeat is None or heartbeat < stale_before

    def _requeue(self, scan_id: UUID, submit: Callable[[UUID], None]) -> None:
        try:
            submit(scan_id)
            logger.info("scan_id=%s action=recovery_requeued", scan_id)
        except ScanQueueFullError:
            self.mark_failed(
                scan_id, "검사 대기열이 가득 찼습니다. 다시 시도해 주세요.", code="QueueUnavailable"
            )
            logger.info("scan_id=%s action=recovery_queue_full", scan_id)

    def _fail_interrupted(self, scan_id: UUID) -> None:
        with self.session_factory() as session:
            won = ScanRepository(session).fail_if_running(scan_id)
        if won:
            self.mark_failed(
                scan_id,
                "검사가 중단되어 실패로 처리했습니다. 다시 시도해 주세요.",
                code="Interrupted",
            )
            logger.info("scan_id=%s action=recovery_interrupted", scan_id)

    # ---------------------------------------------------------------- owner-initiated retry

    def retry(
        self, user: User, scan_id: UUID, upload_id: str, acknowledge_warnings: bool
    ) -> ScanAccepted:
        """Start a new attempt of the caller's failed scan from a freshly prepared source.

        The existing module version is reused (no duplicate repository, version or findings),
        the previous failed scan is kept and linked, and a conditional UPDATE guards against a
        repeated or concurrent retry producing more than one new attempt.
        """
        with self.session_factory() as session:
            scans = ScanRepository(session)
            old = scans.get(scan_id)
            version = (
                session.get(ModuleVersion, old.module_version_id) if old.module_version_id else None
            )
            repository = (
                session.get(Repository, version.repository_id)
                if version and version.repository_id
                else None
            )
            # Only the owner retries; others (even of a public scan) are told it does not exist.
            if repository is None or repository.owner_id != user.id:
                raise NotFoundError("Scan not found.")
            if old.status != ScanStatus.FAILED:
                raise ConflictError("실패한 검사만 다시 시도할 수 있습니다.")
            # A missing or expired prepared source answers "upload again" (StagedUploadNotFound),
            # before anything is created, so a gone source leaves no trace.
            report = self.staging.peek(user.id, upload_id)
            if report.get("warnings") and not acknowledge_warnings:
                raise UploadRejectedError(
                    "확인이 필요한 항목이 있습니다. 비밀값이 아님을 확인한 뒤 올려 주세요.",
                    "WarningsNotAcknowledged",
                )
            if scans.running_publishes_for(user.id) >= self.settings.max_running_publishes:
                raise RateLimitedError("진행 중인 올리기가 많습니다. 끝난 뒤 다시 시도해 주세요.")
            attempt = ((old.analysis or {}).get("runtime") or {}).get("attempt", 1) + 1
            new = scans.create_retry(old, attempt)
            # Claim the retry atomically: a second concurrent or repeated attempt finds the
            # version already pointed elsewhere and is refused, so only one new scan is created.
            if not scans.claim_retry(version.id, scan_id, new.id):
                session.rollback()
                raise ConflictError("이미 다시 시도된 검사입니다.")
            old.analysis = {**(old.analysis or {}), "retried_by": str(new.id)}
            self.staging.claim(user.id, upload_id, new.id)
            try:
                session.commit()
            except BaseException:
                self.staging.release(new.id)
                raise
            logger.info("scan_id=%s action=retry_created retry_of=%s", new.id, scan_id)
            return ScanAccepted(scan_id=new.id)


ScanRunner = Callable[[UUID], None]
