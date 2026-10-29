from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class IntegrityInfo(BaseModel):
    version_id: UUID | None = None
    scan_id: UUID | None = None
    source_url: str | None = None
    source_commit_sha: str | None = None
    source_version: str | None = None
    module_path: str = "."
    manifest_sha256: str | None = None
    snapshot_sha256: str | None = None
    zip_sha256: str | None = None
    artifact_integrity_status: Literal["VERIFIED", "MISMATCH", "UNVERIFIED", "ERROR"] = "UNVERIFIED"
    collected_at: datetime | None = None
    verified_at: datetime | None = None
    integrity_status: Literal["VERIFIED", "MISMATCH", "UNVERIFIED", "ERROR"] = "UNVERIFIED"
    mismatched_files: list[str] = Field(default_factory=list)
    download_allowed: bool = False

    @classmethod
    def of(cls, version):
        if version is None:
            return cls()
        status = (
            version.integrity_status
            if version.manifest_sha256 and version.manifest
            else "UNVERIFIED"
        )
        return cls(
            version_id=version.id,
            scan_id=version.scan_id,
            source_url=version.source_url,
            source_commit_sha=version.commit_hash,
            source_version=version.source_ref,
            module_path=version.module_path,
            manifest_sha256=version.manifest_sha256,
            snapshot_sha256=version.snapshot_sha256,
            collected_at=version.collected_at,
            verified_at=version.verified_at,
            integrity_status=status,
            mismatched_files=version.mismatched_files or [],
            download_allowed=status == "VERIFIED" and bool(version.snapshot_sha256),
        )


class IntegrityFile(BaseModel):
    path: str
    sha256: str
    size: int
    mode: int


class ArtifactInfo(BaseModel):
    artifact_id: UUID
    version_id: UUID
    zip_sha256: str
    artifact_size: int
    filename: str
    integrity_status: str = "VERIFIED"


class IntegrityErrorDetail(BaseModel):
    path: str


class IntegrityErrorBody(BaseModel):
    code: Literal["IntegrityMismatch", "IntegrityUnverified", "IntegrityError"]
    message: str
    details: list[IntegrityErrorDetail] = Field(default_factory=list)


class IntegrityErrorResponse(BaseModel):
    error: IntegrityErrorBody
