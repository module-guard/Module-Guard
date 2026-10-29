import io
import logging
import re
from collections.abc import Iterator
from dataclasses import dataclass
from typing import IO
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError as DatabaseIntegrityError
from sqlalchemy.orm import Session, sessionmaker, undefer

from app.core.config import Settings
from app.core.exceptions import NotFoundError, UploadRejectedError
from app.models import DownloadArtifact, ModuleVersion, Repository
from app.models.enums import OriginType, ScanStatus
from app.schemas.integrity import ArtifactInfo
from app.schemas.usage import UsagePreview, UsageRequest
from app.services.integrity_service import verify_version
from app.services.social_service import find_visible_repository
from app.storage.integrity import IntegrityError, digest, manifest_bytes
from app.storage.snapshots import SnapshotStore
from app.uploads.limits import HourlyLimiter
from app.usage.archive import CHUNK, build_download
from app.usage.hcl import Usage, UsageValueError, module_label, render_usage

logger = logging.getLogger(__name__)
GITHUB_URL = re.compile(r"^https://github\.com/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$")
COMMIT = re.compile(r"^[0-9a-f]{7,64}$")
SAFE = re.compile(r"[^A-Za-z0-9_-]")


@dataclass
class Download:
    filename: str
    body: Iterator[bytes]
    sha256: str = ""
    artifact_id: UUID | None = None


def _stream(handle: IO[bytes]) -> Iterator[bytes]:
    try:
        while chunk := handle.read(CHUNK):
            yield chunk
    finally:
        handle.close()


class UsageService:
    """Usage code and downloads for a module version the caller is allowed to see.

    Generated non-sensitive form files are stored only in the immutable ZIP. Logs carry who, which module and
    sizes only, and errors name the variable without repeating what was sent.
    """

    def __init__(
        self,
        session_factory: sessionmaker[Session],
        settings: Settings,
        snapshot_store: SnapshotStore,
    ):
        self.session_factory = session_factory
        self.snapshot_store = snapshot_store
        self.preview_limiter = HourlyLimiter(settings.usage_previews_per_hour)
        self.download_limiter = HourlyLimiter(settings.downloads_per_hour)

    @staticmethod
    def _version(session: Session, repository: Repository, label: str | None) -> ModuleVersion:
        query = (
            select(ModuleVersion)
            .where(
                ModuleVersion.repository_id == repository.id,
                ModuleVersion.snapshot_sha256.is_not(None),
            )
            .order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc())
        )
        if label is not None:
            query = query.where(ModuleVersion.version == label)
        version = session.scalars(query).first()
        if version is None:
            raise NotFoundError("Version not found.")
        return version

    @staticmethod
    def _filename(repository: Repository, version: ModuleVersion) -> str:
        """Built from characters we allow only, so it is safe inside a response header."""
        name = SAFE.sub("", repository.name)[:100] or "module"
        label = SAFE.sub("", version.version or "")[:20]
        return f"{name}-{label}.zip" if label else f"{name}.zip"

    @staticmethod
    def _git_source(repository: Repository, version: ModuleVersion) -> str | None:
        url, commit = repository.origin_url or "", version.commit_hash or ""
        if repository.origin_type != OriginType.GITHUB:
            return None
        if not GITHUB_URL.match(url) or not COMMIT.match(commit):
            return None
        return f"git::{url}.git?ref={commit}"

    def _render(self, repository: Repository, version: ModuleVersion, request: UsageRequest):
        try:
            return render_usage(
                repository.name, version.variables or [], request.values, request.pass_sensitive
            )
        except UsageValueError as error:
            details = {"variable": error.variable} if error.variable else None
            raise UploadRejectedError(error.reason, "InvalidValue", details) from None

    def _load(self, viewer_id: UUID | None, owner: str, name: str, request: UsageRequest):
        with self.session_factory() as session:
            repository = find_visible_repository(session, owner, name, viewer_id)
            version = self._version(session, repository, request.version)
            usage: Usage = self._render(repository, version, request)
            return (
                usage,
                repository.id,
                module_label(repository.name),
                version.version or "",
                version.snapshot_sha256,
                self._filename(repository, version),
                self._git_source(repository, version),
            )

    def preview(
        self, viewer_id: UUID | None, caller: str, owner: str, name: str, request: UsageRequest
    ) -> UsagePreview:
        self.preview_limiter.check(caller, "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요.")
        usage, _, _, label, _, filename, git_source = self._load(viewer_id, owner, name, request)
        return UsagePreview(
            version=label,
            filename=filename,
            main_tf=usage.main_tf,
            variables_tf=usage.variables_tf,
            missing_required=usage.missing_required,
            sensitive=usage.sensitive,
            git_source=git_source,
        )

    def _artifact(self, viewer_id, owner, name, request):
        with self.session_factory() as session:
            repository = find_visible_repository(session, owner, name, viewer_id)
            version = self._version(session, repository, request.version)
            usage = self._render(repository, version, request)
            from app.models import Scan

            scan = session.get(Scan, version.scan_id) if version.scan_id else None
            if scan is None or scan.status != ScanStatus.COMPLETED:
                raise IntegrityError("UNVERIFIED")
            snapshot = verify_version(session, version, self.snapshot_store)
            files = {"main.tf": usage.main_tf}
            if usage.variables_tf:
                files["variables.tf"] = usage.variables_tf
            key = digest(
                manifest_bytes(
                    {"format": 1, "label": module_label(repository.name), "files": files}
                )
            )
            query = (
                select(DownloadArtifact)
                .options(undefer(DownloadArtifact.content))
                .where(
                    DownloadArtifact.version_id == version.id,
                    DownloadArtifact.request_sha256 == key,
                )
            )
            artifact = session.scalar(query)
            if request.artifact_id is not None and (
                artifact is None or artifact.id != request.artifact_id
            ):
                raise IntegrityError()
            if artifact is None:
                with build_download(snapshot, module_label(repository.name), files) as built:
                    content = built.read()
                artifact = DownloadArtifact(
                    version_id=version.id,
                    request_sha256=key,
                    zip_sha256=digest(content),
                    artifact_size=len(content),
                    content=content,
                )
                try:
                    with session.begin_nested():
                        session.add(artifact)
                        session.flush()
                    session.commit()
                except DatabaseIntegrityError:
                    # A concurrent identical request published first; use its immutable bytes.
                    artifact = session.scalar(query)
                    if artifact is None:
                        raise
            content = artifact.content
            if (
                artifact.integrity_status == "MISMATCH"
                or digest(content) != artifact.zip_sha256
                or len(content) != artifact.artifact_size
            ):
                artifact.integrity_status = "MISMATCH"
                session.commit()
                raise IntegrityError()
            info = ArtifactInfo(
                artifact_id=artifact.id,
                version_id=version.id,
                zip_sha256=artifact.zip_sha256,
                artifact_size=artifact.artifact_size,
                filename=self._filename(repository, version),
            )
            return info, content

    def prepare_download(self, viewer_id, caller, owner, name, request) -> ArtifactInfo:
        self.preview_limiter.check(caller, "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요.")
        return self._artifact(viewer_id, owner, name, request)[0]

    def download(
        self, viewer_id: UUID | None, caller: str, owner: str, name: str, request: UsageRequest
    ) -> Download:
        # Access checks happen before consuming the allowance, as in the original API.
        self._load(viewer_id, owner, name, request)
        self.download_limiter.check(caller, "다운로드가 너무 많습니다. 잠시 뒤 다시 시도해 주세요.")
        info, content = self._artifact(viewer_id, owner, name, request)
        logger.info(
            "action=module_downloaded version_id=%s by=%s inputs=%d",
            info.version_id,
            viewer_id or "anonymous",
            len(request.values),
        )
        return Download(
            info.filename, _stream(io.BytesIO(content)), info.zip_sha256, info.artifact_id
        )
