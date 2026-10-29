from fastapi import APIRouter, Depends, Request, Response
from fastapi.responses import StreamingResponse

from app.auth.dependencies import OptionalUser, verify_origin
from app.core.exceptions import UploadTooLargeError
from app.schemas.integrity import ArtifactInfo, IntegrityErrorResponse
from app.schemas.usage import UsagePreview, UsageRequest

MAX_BODY_BYTES = 256 * 1024
NO_STORE = {"Cache-Control": "no-store"}


def limit_body(request: Request) -> None:
    declared = request.headers.get("content-length", "")
    if not declared.isdigit() or int(declared) > MAX_BODY_BYTES:
        raise UploadTooLargeError("입력값이 너무 큽니다.", "TooLarge")


# Values travel in the body (never the URL), and the cookie may unlock a private module.
router = APIRouter(
    tags=["usage"],
    dependencies=[Depends(verify_origin), Depends(limit_body)],
    responses={409: {"model": IntegrityErrorResponse}},
)


def _caller(request: Request, user) -> tuple[object | None, str]:
    """Who to count against the hourly limit: the user, or the address when signed out."""
    if user:
        return user.id, f"user:{user.id}"
    return None, f"ip:{request.client.host if request.client else 'unknown'}"


@router.post("/repositories/{owner}/{name}/usage", response_model=UsagePreview)
def usage(
    owner: str,
    name: str,
    payload: UsageRequest,
    request: Request,
    response: Response,
    user: OptionalUser,
):
    """Usage code for a module the caller may see. Nothing sent here is stored or logged."""
    viewer, caller = _caller(request, user)
    response.headers.update(NO_STORE)
    return request.app.state.usage_service.preview(viewer, caller, owner, name, payload)


@router.post(
    "/repositories/{owner}/{name}/download",
    response_class=StreamingResponse,
    responses={200: {"content": {"application/zip": {}}}},
)
def download(owner: str, name: str, payload: UsageRequest, request: Request, user: OptionalUser):
    """modules/<name>/ (the stored snapshot) plus a root main.tf that uses it."""
    viewer, caller = _caller(request, user)
    built = request.app.state.usage_service.download(viewer, caller, owner, name, payload)
    return StreamingResponse(
        built.body,
        media_type="application/zip",
        headers={
            **NO_STORE,
            "Content-Disposition": f'attachment; filename="{built.filename}"',
            "X-Content-Type-Options": "nosniff",
            "X-Content-SHA256": built.sha256,
            "X-Artifact-ID": str(built.artifact_id),
        },
    )


@router.post("/repositories/{owner}/{name}/download/prepare", response_model=ArtifactInfo)
def prepare_download(
    owner: str,
    name: str,
    payload: UsageRequest,
    request: Request,
    response: Response,
    user: OptionalUser,
):
    viewer, caller = _caller(request, user)
    response.headers.update(NO_STORE)
    return request.app.state.usage_service.prepare_download(viewer, caller, owner, name, payload)
