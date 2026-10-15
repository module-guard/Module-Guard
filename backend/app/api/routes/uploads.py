from fastapi import APIRouter, Depends, Request
from starlette.concurrency import run_in_threadpool

from app.auth.dependencies import CurrentUser, verify_origin
from app.core.exceptions import UploadRejectedError, UploadTooLargeError
from app.schemas.publish import GitHubImportRequest, RegistryImportRequest, UploadReport

router = APIRouter(prefix="/uploads", tags=["uploads"], dependencies=[Depends(verify_origin)])
ZIP_TYPES = {"application/zip", "application/x-zip-compressed", "application/octet-stream"}


@router.post("/zip", response_model=UploadReport)
async def prepare_zip(request: Request, user: CurrentUser, filename: str | None = None):
    """Body is the zip itself. The size limit is enforced while the bytes arrive."""
    service = request.app.state.publish_service
    content_type = request.headers.get("content-type", "").split(";")[0].strip().lower()
    if content_type not in ZIP_TYPES:
        raise UploadRejectedError("zip 파일만 올릴 수 있습니다.", "InvalidArchive")
    too_large = UploadTooLargeError(
        f"zip 파일은 {service.settings.max_upload_zip_mb}MB까지 올릴 수 있습니다.",
        "ArchiveTooLarge",
    )
    declared = request.headers.get("content-length")
    if declared is not None and (not declared.isdigit() or int(declared) > service.max_zip_bytes):
        raise too_large
    with service.limiter.slot(user.id):
        path = service.incoming_path(user.id)
        received = 0
        try:
            with open(path, "wb") as output:
                async for chunk in request.stream():
                    received += len(chunk)
                    if received > service.max_zip_bytes:
                        # Stop reading: the rest of the body is never accepted.
                        raise too_large
                    output.write(chunk)
        except BaseException:
            path.unlink(missing_ok=True)
            raise
        return await run_in_threadpool(service.prepare_zip, user.id, path, filename)


@router.post("/github", response_model=UploadReport)
async def prepare_github(payload: GitHubImportRequest, request: Request, user: CurrentUser):
    service = request.app.state.publish_service
    with service.limiter.slot(user.id):
        return await run_in_threadpool(service.prepare_github, user.id, payload.url)


@router.post("/registry", response_model=UploadReport)
async def prepare_registry(payload: RegistryImportRequest, request: Request, user: CurrentUser):
    service = request.app.state.publish_service
    with service.limiter.slot(user.id):
        return await run_in_threadpool(
            service.prepare_registry, user.id, payload.address, payload.version
        )
