import logging
import re
import shutil
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

from sqlalchemy import delete, exists, func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session, sessionmaker

from app.analyzers.module_interface import detect_license
from app.auth.visibility import repository_visible_to
from app.collectors.base import SourceCollector
from app.collectors.router import default_router
from app.collectors.source_detector import detect_source, normalize_github_url
from app.core.config import Settings
from app.core.exceptions import (
    ConflictError,
    NotFoundError,
    RateLimitedError,
    UploadRejectedError,
    UploadTooLargeError,
)
from app.models import Module, ModuleVersion, Repository, Scan, Star, User
from app.models.common import utcnow
from app.models.enums import OriginType, ScanStatus, SourceType, Visibility
from app.repositories.module_repository import ModuleRepository
from app.schemas.publish import (
    MyRepository,
    RepositoryCreate,
    RepositoryCreated,
    RepositoryDetail,
    RepositoryEdit,
    UploadReport,
    VersionCreate,
    VersionDetail,
    VersionSummary,
)
from app.schemas.scan import LatestScanSummary
from app.schemas.social import RepositoryRef
from app.uploads.archive import ArchiveLimits, extract_zip
from app.uploads.limits import PreflightLimiter
from app.uploads.policy import REPORT_LIMIT, SourceReport, filter_tree
from app.uploads.secrets import SecretInspector, refuse_confirmed
from app.uploads.staging import TREE, StagingStore

logger = logging.getLogger(__name__)
SLUG = re.compile(r"[^a-z0-9_-]+")


def suggest_name(value: str | None) -> str:
    stem = (value or "").rsplit("/", 1)[-1].lower()
    for suffix in (".zip", ".git"):
        stem = stem.removesuffix(suffix)
    return SLUG.sub("-", stem).strip("-_")[:100] or "module"


class PublishService:
    """Prepare (inspect and hold) a submitted source, then register it as a repository."""

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        collector: SourceCollector | None = None,
        inspector: SecretInspector | None = None,
        staging: StagingStore | None = None,
        limiter: PreflightLimiter | None = None,
    ):
        self.session_factory = session_factory
        self.settings = settings
        self.collector = collector or default_router(settings)
        self.inspector = inspector or SecretInspector(settings)
        self.staging = staging or StagingStore(
            settings.upload_root, settings.upload_ttl_minutes * 60
        )
        self.limiter = limiter or PreflightLimiter(settings.upload_preflights_per_hour)
        self.archive_limits = ArchiveLimits(
            max_uncompressed_bytes=settings.max_upload_uncompressed_mb * 1024**2,
            max_files=settings.max_upload_files,
        )

    @property
    def max_zip_bytes(self) -> int:
        return self.settings.max_upload_zip_mb * 1024**2

    def incoming_path(self, user_id: UUID) -> Path:
        """Where the request body is streamed before inspection (one file per user)."""
        directory = self.settings.upload_root / "incoming"
        directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        return directory / f"{UUID(str(user_id))}.zip"

    def prepare_zip(self, user_id: UUID, archive: Path, filename: str | None) -> UploadReport:
        token, slot = self.staging.open(user_id)
        try:
            report = extract_zip(archive, slot / TREE, self.archive_limits)
            return self._finish(slot, token, report, "UPLOAD", None, None, suggest_name(filename))
        except BaseException:
            self.staging.discard(slot)
            raise
        finally:
            archive.unlink(missing_ok=True)

    def prepare_github(
        self,
        user_id: UUID,
        url: str,
        ref: str | None = None,
        expected_commit: str | None = None,
    ) -> UploadReport:
        """Fetch, filter and inspect a public repository.

        `ref` pins a release tag or commit. With `expected_commit` the fetched commit must be
        that one: a tag that has been moved since it was reviewed is refused.
        """
        normalized = normalize_github_url(url)
        token, slot = self.staging.open(user_id)
        try:
            collected = self.collector.collect(normalized, slot / TREE, ref)
            if expected_commit is not None and collected.commit_hash != expected_commit:
                raise UploadRejectedError(
                    "받아 온 커밋이 고정한 커밋과 다릅니다.", "PinnedCommitMismatch"
                )
            return self._finish_source(slot, token, collected, "GITHUB", ref)
        except BaseException:
            self.staging.discard(slot)
            raise

    def prepare_registry(
        self, user_id: UUID, address: str, version: str | None = None
    ) -> UploadReport:
        """Resolve a Terraform Registry address to its source, then inspect it like a repository.

        The backing repository is cloned and checked exactly as a GitHub import is; only the
        way the source was named differs.
        """
        token, slot = self.staging.open(user_id)
        try:
            collected = self.collector.collect(address, slot / TREE, version)
            return self._finish_source(slot, token, collected, "GITHUB", version)
        except BaseException:
            self.staging.discard(slot)
            raise

    def _finish_source(
        self, slot: Path, token: str, collected, origin_type: str, source_ref: str | None
    ) -> UploadReport:
        """Filter and size-check a cloned tree, then seal it under a one-time token."""
        shutil.rmtree(slot / TREE / ".git", ignore_errors=True)
        report = filter_tree(slot / TREE)
        if report.size_bytes > self.archive_limits.max_uncompressed_bytes:
            raise UploadTooLargeError("저장소 크기가 제한을 넘습니다.", "UncompressedTooLarge")
        if len(report.included) > self.archive_limits.max_files:
            raise UploadTooLargeError(
                f"파일은 {self.archive_limits.max_files}개까지 올릴 수 있습니다.",
                "TooManyFiles",
            )
        return self._finish(
            slot,
            token,
            report,
            origin_type,
            collected.source_url,
            collected.commit_hash,
            suggest_name(collected.repository_name),
            branch=collected.branch,
            source_ref=source_ref,
        )

    def _finish(
        self,
        slot: Path,
        token: str,
        report: SourceReport,
        origin_type: str,
        origin_url: str | None,
        commit_hash: str | None,
        suggested_name: str,
        branch: str | None = None,
        source_ref: str | None = None,
    ) -> UploadReport:
        # Definite secrets refuse the upload here: nothing has been stored outside this slot,
        # and the caller discards the slot.
        warnings = refuse_confirmed(self.inspector.inspect(slot / TREE, report.included))
        expires_at = self.staging.seal(
            slot,
            {
                "origin_type": origin_type,
                "origin_url": origin_url,
                "commit_hash": commit_hash,
                "branch": branch,
                "source_ref": source_ref,
                "file_count": len(report.included),
                # Locations only: previews are returned once and never written to disk.
                "warnings": [{"file": w.file, "line": w.line, "rule": w.rule} for w in warnings],
            },
        )
        logger.info(
            "action=upload_prepared origin=%s files=%s excluded=%s warnings=%s",
            origin_type,
            len(report.included),
            len(report.excluded),
            len(warnings),
        )
        return UploadReport(
            upload_id=token,
            origin_type=origin_type,
            origin_url=origin_url,
            commit_hash=commit_hash,
            suggested_name=suggested_name,
            license=detect_license(slot / TREE),
            file_count=len(report.included),
            size_bytes=report.size_bytes,
            excluded=report.excluded[:REPORT_LIMIT],
            excluded_count=len(report.excluded),
            warnings=[
                {
                    "file": w.file,
                    "line": w.line,
                    "rule": w.rule,
                    "title": w.title,
                    "preview": w.preview,
                }
                for w in warnings
            ],
            expires_at=datetime.fromtimestamp(expires_at, UTC),
        )

    def register(
        self, user: User, payload: RepositoryCreate, scanner_name: str
    ) -> RepositoryCreated:
        """Create the repository, its first version and the scan of the prepared source.

        The prepared tree is claimed last and atomically, so a token is spent at most once and
        only when everything else about the request is valid.
        """
        report = self._checked_report(user.id, payload.upload_id, payload.acknowledge_warnings)
        with self.session_factory() as session:
            self._check_running_limit(session, user.id)
            existing = session.scalar(
                select(Repository).where(
                    Repository.owner_id == user.id, Repository.name == payload.name
                )
            )
            if existing is not None:
                if self._usable_versions(session, existing.id) or self._running_scans(
                    session, existing.id
                ):
                    raise ConflictError("같은 이름의 모듈이 이미 있습니다.")
                # A previous attempt that never produced a version: its name is free again.
                self._discard(session, existing)
            repository = Repository(
                owner_id=user.id,
                name=payload.name,
                description=payload.description or None,
                visibility=payload.visibility,
                origin_type=(
                    OriginType.GITHUB if report["origin_type"] == "GITHUB" else OriginType.UPLOAD
                ),
                origin_url=report["origin_url"],
            )
            session.add(repository)
            try:
                session.flush()
            except IntegrityError:
                raise ConflictError("같은 이름의 모듈이 이미 있습니다.") from None
            return self._start_version(
                session, user, repository, report, "v1", payload.upload_id, scanner_name
            )

    def register_version(
        self, user: User, name: str, payload: VersionCreate, scanner_name: str
    ) -> RepositoryCreated:
        """Add the next version of one of the caller's repositories from a prepared source."""
        report = self._checked_report(user.id, payload.upload_id, payload.acknowledge_warnings)
        with self.session_factory() as session:
            repository = session.scalar(
                select(Repository).where(Repository.owner_id == user.id, Repository.name == name)
            )
            if repository is None:
                raise NotFoundError("Repository not found.")
            if self._running_scans(session, repository.id):
                raise ConflictError("검사가 진행 중입니다. 끝난 뒤 다시 올려 주세요.")
            self._check_running_limit(session, user.id)
            # Attempts that never got stored are dropped so version numbers have no gaps.
            failed = select(ModuleVersion.id).where(
                ModuleVersion.repository_id == repository.id,
                ModuleVersion.snapshot_sha256.is_(None),
            )
            session.execute(delete(Scan).where(Scan.module_version_id.in_(failed)))
            session.execute(
                delete(ModuleVersion).where(
                    ModuleVersion.repository_id == repository.id,
                    ModuleVersion.snapshot_sha256.is_(None),
                )
            )
            labels = session.scalars(
                select(ModuleVersion.version).where(ModuleVersion.repository_id == repository.id)
            )
            numbers = [int(label[1:]) for label in labels if label and label[1:].isdigit()]
            repository.updated_at = utcnow()
            return self._start_version(
                session,
                user,
                repository,
                report,
                f"v{max(numbers, default=0) + 1}",
                payload.upload_id,
                scanner_name,
            )

    def _checked_report(self, user_id: UUID, token: str, acknowledged: bool) -> dict:
        report = self.staging.peek(user_id, token)
        if report["warnings"] and not acknowledged:
            raise UploadRejectedError(
                "확인이 필요한 항목이 있습니다. 비밀값이 아님을 확인한 뒤 올려 주세요.",
                "WarningsNotAcknowledged",
            )
        return report

    def _check_running_limit(self, session: Session, user_id: UUID) -> None:
        running = session.scalar(
            select(func.count(Scan.id))
            .join(ModuleVersion, ModuleVersion.id == Scan.module_version_id)
            .join(Repository, Repository.id == ModuleVersion.repository_id)
            .where(
                Repository.owner_id == user_id,
                Scan.status.notin_([ScanStatus.COMPLETED, ScanStatus.FAILED]),
            )
        )
        if running >= self.settings.max_running_publishes:
            raise RateLimitedError("진행 중인 올리기가 많습니다. 끝난 뒤 다시 시도해 주세요.")

    def _start_version(
        self,
        session: Session,
        user: User,
        repository: Repository,
        report: dict,
        label: str,
        token: str,
        scanner_name: str,
    ) -> RepositoryCreated:
        """Version row + scan for the prepared tree. The token is claimed last, atomically."""
        version = ModuleVersion(
            repository_id=repository.id,
            version=label,
            commit_hash=report["commit_hash"],
            source_ref=report.get("source_ref"),
        )
        session.add(version)
        if report["origin_type"] == "GITHUB":
            module = ModuleRepository(session).get_or_create(detect_source(report["origin_url"]))
        else:
            source = f"upload://{repository.id}"
            module = session.scalar(select(Module).where(Module.source_url == source))
            if module is None:
                module = Module(
                    name=repository.name,
                    source_type=SourceType.UPLOAD,
                    source_url=source,
                    repository_owner=user.login,
                    repository_name=repository.name,
                )
                session.add(module)
        session.flush()
        scan = Scan(module_id=module.id, module_version_id=version.id, scanner=scanner_name)
        session.add(scan)
        session.flush()
        self.staging.claim(user.id, token, scan.id)
        try:
            session.commit()
        except BaseException:
            self.staging.release(scan.id)
            raise
        return RepositoryCreated(
            owner=user.login,
            name=repository.name,
            version=label,
            visibility=repository.visibility,
            scan_id=scan.id,
        )

    @staticmethod
    def _usable_versions(session: Session, repository_id: UUID) -> int:
        return session.scalar(
            select(func.count(ModuleVersion.id)).where(
                ModuleVersion.repository_id == repository_id,
                ModuleVersion.snapshot_sha256.is_not(None),
            )
        )

    @staticmethod
    def _running_scans(session: Session, repository_id: UUID) -> int:
        return session.scalar(
            select(func.count(Scan.id))
            .join(ModuleVersion, ModuleVersion.id == Scan.module_version_id)
            .where(
                ModuleVersion.repository_id == repository_id,
                Scan.status.notin_([ScanStatus.COMPLETED, ScanStatus.FAILED]),
            )
        )

    @staticmethod
    def _discard(session: Session, repository: Repository) -> None:
        """Remove a repository and what only it used.

        Scans (and the internal module row of an upload) are shared with forks through
        ModuleVersion.scan_id, so they are deleted only when no other repository's version
        still points at them. Snapshot files are never touched here: see storage/retention.py.
        """
        own = select(ModuleVersion.id).where(ModuleVersion.repository_id == repository.id)
        scan_ids = set(
            session.scalars(select(Scan.id).where(Scan.module_version_id.in_(own)))
        ) | set(
            session.scalars(
                select(ModuleVersion.scan_id).where(
                    ModuleVersion.repository_id == repository.id,
                    ModuleVersion.scan_id.is_not(None),
                )
            )
        )
        shared = set(
            session.scalars(
                select(ModuleVersion.scan_id).where(
                    ModuleVersion.scan_id.in_(scan_ids),
                    ModuleVersion.repository_id.is_not(None),
                    ModuleVersion.repository_id != repository.id,
                )
            )
        )
        if repository.forked_from_id is not None:
            session.execute(
                update(Repository)
                .where(Repository.id == repository.forked_from_id, Repository.fork_count > 0)
                .values(fork_count=Repository.fork_count - 1)
            )
        doomed = scan_ids - shared
        # Internal module rows of uploads: this repository's own, and those of the scans
        # being removed (a fork can outlive its origin and be the last user of its scan).
        module_ids = set(session.scalars(select(Scan.module_id).where(Scan.id.in_(doomed)))) | set(
            session.scalars(
                select(Module.id).where(Module.source_url == f"upload://{repository.id}")
            )
        )
        session.execute(delete(Scan).where(Scan.id.in_(doomed)))
        session.execute(
            delete(Module).where(
                Module.id.in_(module_ids),
                Module.source_type == SourceType.UPLOAD,
                ~exists(select(Scan.id).where(Scan.module_id == Module.id)),
            )
        )
        session.delete(repository)
        session.flush()

    def _own(self, session: Session, user_id: UUID, name: str) -> Repository:
        repository = session.scalar(
            select(Repository).where(Repository.owner_id == user_id, Repository.name == name)
        )
        if repository is None:
            raise NotFoundError("Repository not found.")
        return repository

    def delete(self, user_id: UUID, name: str, confirm: str | None = None) -> None:
        """Delete one of the caller's repositories. Forks of it keep working.

        A repository with a stored version is only deleted when the caller repeats its
        name; a failed attempt that was never published needs no confirmation.
        """
        with self.session_factory() as session:
            repository = self._own(session, user_id, name)
            if self._usable_versions(session, repository.id) and confirm != repository.name:
                raise UploadRejectedError(
                    "삭제하려면 모듈 이름을 정확히 입력해 주세요.", "ConfirmationRequired"
                )
            if self._running_scans(session, repository.id):
                raise ConflictError("검사가 진행 중입니다. 끝난 뒤 다시 시도해 주세요.")
            self._discard(session, repository)
            session.commit()

    def edit(self, user_id: UUID, name: str, changes: RepositoryEdit) -> None:
        with self.session_factory() as session:
            repository = self._own(session, user_id, name)
            if "description" in changes.model_fields_set:
                repository.description = changes.description or None
            if changes.visibility is not None:
                repository.visibility = changes.visibility
            renamed = changes.name is not None and changes.name != repository.name
            try:
                if renamed:
                    repository.name = changes.name
                    session.flush()
                    # The scan screens label an uploaded module by its own row; keep it in step.
                    session.execute(
                        update(Module)
                        .where(Module.source_url == f"upload://{repository.id}")
                        .values(name=changes.name, repository_name=changes.name)
                    )
                session.commit()
            except IntegrityError:
                raise ConflictError("같은 이름의 모듈이 이미 있습니다.") from None

    def mine(self, user_id: UUID) -> list[MyRepository]:
        with self.session_factory() as session:
            repositories = list(
                session.scalars(
                    select(Repository)
                    .where(Repository.owner_id == user_id)
                    .order_by(Repository.created_at.desc(), Repository.id.desc())
                )
            )
            result = []
            for repository in repositories:
                version = session.scalar(
                    select(ModuleVersion)
                    .where(ModuleVersion.repository_id == repository.id)
                    .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
                    .limit(1)
                )
                scan = (
                    session.scalar(
                        select(Scan)
                        .where(Scan.module_version_id == version.id)
                        .order_by(Scan.created_at.desc())
                        .limit(1)
                    )
                    if version
                    else None
                )
                published = bool(self._usable_versions(session, repository.id))
                failed = not published and scan is not None and scan.status == ScanStatus.FAILED
                result.append(
                    MyRepository(
                        state="PUBLISHED" if published else "FAILED" if failed else "PENDING",
                        failure=scan.error_message if failed else None,
                        name=repository.name,
                        description=repository.description,
                        visibility=repository.visibility,
                        origin_type=repository.origin_type,
                        origin_url=repository.origin_url,
                        created_at=repository.created_at,
                        version=version.version if version else None,
                        latest_scan=LatestScanSummary.from_scan(scan) if scan else None,
                    )
                )
            return result

    def detail(
        self, owner: str, name: str, viewer_id: UUID | None, label: str | None = None
    ) -> RepositoryDetail:
        """A repository as one viewer may see it. Hidden ones are indistinguishable from missing."""
        with self.session_factory() as session:
            repository = session.scalar(
                select(Repository)
                .join(User, User.id == Repository.owner_id)
                .where(func.lower(User.login) == owner.lower(), Repository.name == name)
            )
            is_owner = repository is not None and repository.owner_id == viewer_id
            published = repository is not None and bool(
                self._usable_versions(session, repository.id)
            )
            if repository is None or not (
                is_owner or (repository.visibility == Visibility.PUBLIC and published)
            ):
                raise NotFoundError("Repository not found.")
            rows = list(
                session.scalars(
                    select(ModuleVersion)
                    .where(ModuleVersion.repository_id == repository.id)
                    .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
                )
            )
            # Others only ever see stored versions; the owner also sees attempts.
            visible = [v for v in rows if is_owner or v.snapshot_sha256]
            stored = [v for v in visible if v.snapshot_sha256]
            if label is not None:
                selected = next((v for v in visible if v.version == label), None)
                if selected is None:
                    raise NotFoundError("Version not found.")
            else:
                selected = (stored or visible or [None])[0]
            summaries = {v.id: self._version_summary(session, v) for v in visible}
            owner_row = repository.owner
            origin = (
                session.scalar(
                    select(Repository).where(
                        Repository.id == repository.forked_from_id,
                        repository_visible_to(viewer_id),
                    )
                )
                if repository.forked_from_id
                else None
            )
            return RepositoryDetail(
                owner=owner_row.login,
                mirror=owner_row.is_system,
                owner_name=owner_row.name,
                owner_avatar_url=owner_row.avatar_url,
                name=repository.name,
                description=repository.description,
                provider=repository.provider,
                visibility=repository.visibility,
                origin_type=repository.origin_type,
                origin_url=repository.origin_url,
                star_count=repository.star_count,
                fork_count=repository.fork_count,
                created_at=repository.created_at,
                updated_at=repository.updated_at,
                is_owner=is_owner,
                starred=viewer_id is not None
                and session.get(Star, (viewer_id, repository.id)) is not None,
                forked_from=(
                    RepositoryRef(owner=origin.owner.login, name=origin.name) if origin else None
                ),
                latest_version=stored[0].version if stored else None,
                version=(
                    VersionDetail(
                        **summaries[selected.id].model_dump(),
                        readme=selected.readme,
                        variables=selected.variables,
                        outputs=selected.outputs,
                        license=selected.license,
                        source_ref=selected.source_ref,
                        file_count=selected.file_count,
                        size_bytes=selected.size_bytes,
                    )
                    if selected is not None
                    else None
                ),
                versions=[summaries[v.id] for v in visible],
            )

    @staticmethod
    def _version_summary(session: Session, version: ModuleVersion) -> VersionSummary:
        # A fork shares its origin's scan through scan_id; an attempt points back from the scan.
        scan = session.get(Scan, version.scan_id) if version.scan_id else None
        if scan is None:
            scan = session.scalar(
                select(Scan)
                .where(Scan.module_version_id == version.id)
                .order_by(Scan.created_at.desc())
                .limit(1)
            )
        stored = version.snapshot_sha256 is not None
        failed = not stored and scan is not None and scan.status == ScanStatus.FAILED
        return VersionSummary(
            version=version.version,
            created_at=version.created_at,
            commit_hash=version.commit_hash,
            state="STORED" if stored else "FAILED" if failed else "PENDING",
            failure=scan.error_message if failed and scan else None,
            scan=LatestScanSummary.from_scan(scan) if scan else None,
            scanner_breakdown=(
                (scan.scope_summary or {}).get("module_by_scanner")
                or (scan.analysis or {}).get("scanner_breakdown")
                if scan
                else None
            ),
            critical_rules=version.critical_rules,
            high_rules=version.high_rules,
        )
