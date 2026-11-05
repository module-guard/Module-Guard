from uuid import UUID

from fastapi import APIRouter, Depends, Request, Response

from app.api.dependencies import SessionDep
from app.attestations.signing import verify
from app.auth.dependencies import CurrentUser, OptionalUser, verify_origin
from app.core.exceptions import ConflictError
from app.schemas.attestation import AttestationProof, AttestationResponse, Verification
from app.services.attestation_service import AttestationService, load_policy
from app.storage.integrity import IntegrityError

router = APIRouter(tags=["attestations"])


def service(request, session):
    return AttestationService(session, request.app.state.scan_service.snapshot_store, load_policy())


@router.get("/{scan_id}/attestations", response_model=AttestationResponse)
def list_attestations(
    scan_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: OptionalUser,
):
    response.headers["Cache-Control"] = "no-store"
    caller = (
        f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    request.app.state.usage_service.preview_limiter.check(
        f"read-attest:{caller}", "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."
    )
    operation = service(request, session)
    scan, version = operation.target(scan_id, version_id, user.id if user else None)
    return operation.describe(scan, version)


@router.post(
    "/{scan_id}/attestations",
    response_model=AttestationResponse,
    status_code=201,
    dependencies=[Depends(verify_origin)],
)
def issue_attestation(
    scan_id: UUID,
    version_id: UUID,
    request: Request,
    response: Response,
    session: SessionDep,
    user: CurrentUser,
):
    response.headers["Cache-Control"] = "no-store"
    request.app.state.usage_service.preview_limiter.check(
        f"attest:{user.id}", "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."
    )
    operation = service(request, session)
    scan, version = operation.target(scan_id, version_id, user.id, owner=True)
    return operation.issue(scan, version)


@router.post(
    "/{scan_id}/attestations/verify",
    response_model=Verification,
    dependencies=[Depends(verify_origin)],
)
def verify_attestation(
    scan_id: UUID,
    version_id: UUID,
    proof: AttestationProof,
    request: Request,
    response: Response,
    session: SessionDep,
    user: OptionalUser,
):
    response.headers["Cache-Control"] = "no-store"
    caller = (
        f"user:{user.id}" if user else f"ip:{request.client.host if request.client else 'unknown'}"
    )
    request.app.state.usage_service.preview_limiter.check(
        f"verify-attest:{caller}", "요청이 너무 많습니다. 잠시 뒤 다시 시도해 주세요."
    )
    operation = service(request, session)
    scan, version = operation.target(scan_id, version_id, user.id if user else None)
    try:
        binding = operation.binding(scan, version)
    except IntegrityError as error:
        return Verification(
            status="invalid" if error.status == "MISMATCH" else "unknown",
            reasons=["CONTENT_MISMATCH" if error.status == "MISMATCH" else "CONTENT_UNAVAILABLE"],
        )
    except (ConflictError, ValueError):
        return Verification(status="unknown", reasons=["TARGET_UNAVAILABLE"])
    return verify(proof, binding, operation.policy)
