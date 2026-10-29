from fastapi import APIRouter, Depends, Query, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import undefer

from app.auth.dependencies import OptionalUser, verify_origin
from app.core.exceptions import NotFoundError
from app.models import DownloadArtifact, ModuleVersion, Scan
from app.models.enums import ScanStatus
from app.schemas.integrity import IntegrityErrorResponse, IntegrityFile, IntegrityInfo
from app.services.integrity_service import verify_version
from app.services.social_service import find_visible_repository
from app.storage.artifacts import wrapper
from app.storage.integrity import IntegrityError, digest

router = APIRouter(tags=["integrity"], responses={409: {"model": IntegrityErrorResponse}})


def load(session, owner, name, user, version):
    repository = find_visible_repository(session, owner, name, user.id if user else None)
    query = select(ModuleVersion).where(ModuleVersion.repository_id == repository.id)
    if version is not None:
        query = query.where(ModuleVersion.version == version)
    row = session.scalar(query.order_by(ModuleVersion.created_at.desc(), ModuleVersion.id.desc()))
    if row is None:
        raise NotFoundError("Version not found.")
    return row


def describe(session, row):
    info = IntegrityInfo.of(row)
    scan = session.get(Scan, row.scan_id) if row.scan_id else None
    info.download_allowed = (
        info.download_allowed and scan is not None and scan.status == ScanStatus.COMPLETED
    )
    if row.repository:
        _, key = wrapper(row.repository.name, row.variables)
        artifact = session.scalar(
            select(DownloadArtifact).where(
                DownloadArtifact.version_id == row.id, DownloadArtifact.request_sha256 == key
            )
        )
        if artifact:
            info.zip_sha256 = artifact.zip_sha256
            info.artifact_integrity_status = artifact.integrity_status
            info.download_allowed = (
                info.download_allowed and artifact.integrity_status == "VERIFIED"
            )
    return info


@router.get("/repositories/{owner}/{name}/integrity", response_model=IntegrityInfo)
def integrity(
    owner: str,
    name: str,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Query(default=None, max_length=100),
):
    response.headers["Cache-Control"] = "no-store"
    with request.app.state.session_factory() as session:
        return describe(session, load(session, owner, name, user, version))


@router.get("/repositories/{owner}/{name}/integrity/files", response_model=list[IntegrityFile])
def files(
    owner: str,
    name: str,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Query(default=None, max_length=100),
):
    response.headers["Cache-Control"] = "no-store"
    with request.app.state.session_factory() as session:
        row = load(session, owner, name, user, version)
        return (row.manifest or {}).get("files", [])


@router.post(
    "/repositories/{owner}/{name}/integrity/verify",
    response_model=IntegrityInfo,
    dependencies=[Depends(verify_origin)],
)
def verify(
    owner: str,
    name: str,
    request: Request,
    response: Response,
    user: OptionalUser,
    version: str | None = Query(default=None, max_length=100),
):
    response.headers["Cache-Control"] = "no-store"
    caller = (
        f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    request.app.state.usage_service.preview_limiter.check(
        caller, "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."
    )
    with request.app.state.session_factory() as session:
        row = load(session, owner, name, user, version)
        scan = session.get(Scan, row.scan_id) if row.scan_id else None
        if scan is None or scan.status != ScanStatus.COMPLETED:
            raise IntegrityError("UNVERIFIED")
        verify_version(session, row, request.app.state.scan_service.snapshot_store)
        if row.repository:
            _, key = wrapper(row.repository.name, row.variables)
            artifact = session.scalar(
                select(DownloadArtifact)
                .options(undefer(DownloadArtifact.content))
                .where(
                    DownloadArtifact.version_id == row.id, DownloadArtifact.request_sha256 == key
                )
            )
            if artifact is not None and (
                artifact.integrity_status == "MISMATCH"
                or digest(artifact.content) != artifact.zip_sha256
                or len(artifact.content) != artifact.artifact_size
            ):
                artifact.integrity_status = "MISMATCH"
                session.commit()
                raise IntegrityError()
        return describe(session, row)
