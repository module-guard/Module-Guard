import json
from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, field_validator, model_validator

AttestationStatus = Literal["unsigned", "valid", "invalid", "unknown", "revoked", "policy-expired"]


class TrustedSigner(BaseModel):
    model_config = ConfigDict(extra="forbid")

    public_key: dict[str, Any]
    issuer: str = Field(min_length=1, max_length=512)
    subject: str = Field(min_length=1, max_length=512)
    purpose: Literal["platform", "upstream"]
    not_before: AwareDatetime
    not_after: AwareDatetime
    revoked: bool = False
    # Fixed key ARN, never an alias or a URI from a submitted proof.
    kms_key_arn: str | None = None

    @model_validator(mode="after")
    def valid_trust(self):
        if self.not_after <= self.not_before:
            raise ValueError("Invalid signer validity interval")
        if set(self.public_key) != {"keytype", "scheme", "keyval"} or set(
            self.public_key["keyval"]
        ) != {"public"}:
            raise ValueError("Only public verification material is allowed")
        if (self.public_key["keytype"], self.public_key["scheme"]) not in {
            ("ecdsa", "ecdsa-sha2-nistp256"),
            ("rsa", "rsassa-pss-sha256"),
        }:
            raise ValueError("Unsupported signing algorithm")
        return self


class AttestationPolicy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: str = Field(min_length=1, max_length=128)
    accepted_claim_versions: list[str] = Field(min_length=1, max_length=32)
    not_before: AwareDatetime
    expires_at: AwareDatetime
    revocations_valid_until: AwareDatetime
    signers: dict[str, TrustedSigner] = Field(min_length=1, max_length=32)
    tsa_roots_pem: list[str] = Field(min_length=1, max_length=8)
    tsa_url: str
    signing_key_id: str | None = None
    ttl_seconds: int = Field(default=86400, ge=60, le=86400)


class Verification(BaseModel):
    status: AttestationStatus
    reasons: list[str] = Field(default_factory=list)
    purpose: Literal["platform", "upstream"] | None = None
    issuer: str | None = None
    subject: str | None = None
    policy_version: str | None = None
    claimed_at: datetime | None = None
    trusted_at: datetime | None = None
    # A valid signature authenticates a claim; it does not assert vulnerability absence.
    security_guarantee: Literal[False] = False


class AttestationProof(BaseModel):
    model_config = ConfigDict(extra="forbid")

    envelope: dict[str, Any]
    timestamp: str | None = Field(default=None, max_length=196608)

    @field_validator("envelope")
    @classmethod
    def bounded_envelope(cls, value):
        if len(json.dumps(value, allow_nan=False).encode()) > 65536:
            raise ValueError("증명 봉투 크기 상한을 초과했습니다.")
        return value


class AttestationEntry(BaseModel):
    id: UUID
    verification: Verification
    proof: AttestationProof


class AttestationResponse(BaseModel):
    schema_version: Literal[1] = 1
    scan_id: UUID
    version_id: UUID
    status: AttestationStatus
    policy_enabled: bool = False
    reasons: list[str] = Field(default_factory=list)
    entries: list[AttestationEntry] = Field(default_factory=list)
