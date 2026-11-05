"""Standard DSSE signatures and RFC 3161 countersignatures, with out-of-band trust."""

import base64
import copy
import hashlib
import json
import re
from datetime import UTC, datetime, timedelta
from urllib.parse import urlsplit

import httpx

from app.core.exceptions import DomainError
from app.schemas.attestation import AttestationPolicy, AttestationProof, Verification

PAYLOAD_TYPE = "application/vnd.in-toto+json"
STATEMENT_TYPE = "https://in-toto.io/Statement/v1"
PREDICATE_TYPE = "https://github.com/module-guard/Module-Guard/attestation/scan/v1"
MAX_PROOF_BYTES = 262144


class AttestationUnavailable(DomainError):
    status_code = 503


class _KMSClient:
    def __init__(self, client):
        self.client = client

    def sign(self, **kwargs):
        try:
            return self.client.sign(**kwargs)
        except Exception:
            # AWSSigner logs SDK exceptions itself: translate before that boundary.
            raise AttestationUnavailable("KMS 서명 서비스를 사용할 수 없습니다.") from None


def canonical(data) -> bytes:
    return json.dumps(
        data, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")


def _unique(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON key")
        result[key] = value
    return result


def strict_json(data: bytes):
    return json.loads(data, object_pairs_hook=_unique, parse_constant=lambda _: _bad_number())


def _bad_number():
    raise ValueError("Non-finite number")


def _time(value: str) -> datetime:
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("Timezone required")
    return parsed.astimezone(UTC)


def _timestamp(signature: bytes, encoded: str, roots: list[str], nonce=None) -> datetime:
    from cryptography.x509 import load_pem_x509_certificate
    from rfc3161_client import VerifierBuilder, decode_timestamp_response

    response = decode_timestamp_response(base64.b64decode(encoded, validate=True))
    # The timestamp's message imprint algorithm is also checked: never hash with an
    # attacker-selected weak algorithm or treat signed_at as independent time evidence.
    if response.tst_info.message_imprint.hash_algorithm.dotted_string != "2.16.840.1.101.3.4.2.1":
        raise ValueError("SHA-256 timestamp required")
    builder = VerifierBuilder()
    for pem in roots:
        builder = builder.add_root_certificate(load_pem_x509_certificate(pem.encode()))
    if nonce is not None:
        builder = builder.nonce(nonce)
    builder.build().verify(response, hashlib.sha256(signature).digest())
    return response.tst_info.gen_time.astimezone(UTC)


def issue(binding: dict, policy: AttestationPolicy, now=None, *, signer=None, timestamp=None):
    """Only the service constructs binding. Submitted claims never reach the KMS signer."""
    from securesystemslib.dsse import Envelope

    now = now or datetime.now(UTC)
    key_id = policy.signing_key_id
    key = policy.signers.get(key_id)
    if (
        key is None
        or key.purpose != "platform"
        or key.revoked
        or not policy.not_before <= now < policy.expires_at
        or now >= policy.revocations_valid_until
        or not key.not_before <= now < key.not_after
        or policy.version not in policy.accepted_claim_versions
    ):
        raise AttestationUnavailable("서명 발급 정책을 사용할 수 없습니다.")
    expires = min(now + timedelta(seconds=policy.ttl_seconds), key.not_after, policy.expires_at)
    statement = {
        "_type": STATEMENT_TYPE,
        "subject": binding["subjects"],
        "predicateType": PREDICATE_TYPE,
        "predicate": {
            "binding": binding,
            "purpose": key.purpose,
            "issuer": key.issuer,
            "subject": key.subject,
            "policy_version": policy.version,
            "issued_at": now.isoformat(),
            "expires_at": expires.isoformat(),
        },
    }
    if signer is None:
        from botocore.config import Config
        from securesystemslib.signer import AWSSigner, Key

        if not key.kms_key_arn or not re.fullmatch(
            r"arn:aws(?:-us-gov|-cn)?:kms:[a-z0-9-]+:\d{12}:key/[A-Za-z0-9-]+",
            key.kms_key_arn,
        ):
            raise AttestationUnavailable("고정 KMS 키가 설정되지 않았습니다.")
        public = Key.from_dict(key_id, copy.deepcopy(key.public_key))
        signer = AWSSigner(key.kms_key_arn, public)
        # Bound KMS requests without changing the library's DSSE/KMS implementation.
        import boto3

        signer.client = _KMSClient(
            boto3.client(
                "kms",
                config=Config(connect_timeout=3, read_timeout=10, retries={"max_attempts": 1}),
            )
        )
    if signer.public_key.keyid != key_id:
        raise AttestationUnavailable("서명 키가 정책과 일치하지 않습니다.")
    envelope = Envelope(canonical(statement), PAYLOAD_TYPE, {})
    signature = envelope.sign(signer)
    raw_signature = bytes.fromhex(signature.signature)
    if timestamp is None:
        from rfc3161_client import HashAlgorithm, TimestampRequestBuilder

        endpoint = urlsplit(policy.tsa_url)
        if (
            endpoint.scheme != "https"
            or not endpoint.hostname
            or endpoint.username is not None
            or endpoint.password is not None
            or endpoint.query
            or endpoint.fragment
        ):
            raise AttestationUnavailable("허용된 HTTPS TSA가 설정되지 않았습니다.")
        request = (
            TimestampRequestBuilder()
            .data(raw_signature)
            .hash_algorithm(HashAlgorithm.SHA256)
            .build()
        )
        with httpx.Client(timeout=10, follow_redirects=False, trust_env=False) as client:
            with client.stream(
                "POST",
                policy.tsa_url,
                content=request.as_bytes(),
                headers={"Content-Type": "application/timestamp-query"},
            ) as response:
                response.raise_for_status()
                chunks, size = [], 0
                for chunk in response.iter_bytes():
                    size += len(chunk)
                    if size > 131072:
                        raise AttestationUnavailable("TSA 응답 상한을 초과했습니다.")
                    chunks.append(chunk)
        encoded = base64.b64encode(b"".join(chunks)).decode()
        _timestamp(raw_signature, encoded, policy.tsa_roots_pem, request.nonce)
    else:
        encoded = timestamp(raw_signature)
    proof = AttestationProof(envelope=envelope.to_dict(), timestamp=encoded)
    checked = verify(proof, binding, policy, now)
    if checked.status != "valid":
        raise AttestationUnavailable("발급한 증명을 검증할 수 없습니다.")
    return proof


def verify(proof: AttestationProof, expected: dict, policy: AttestationPolicy | None, now=None):
    now = now or datetime.now(UTC)
    result = Verification(status="unknown", reasons=["POLICY_UNAVAILABLE"])
    if policy is None:
        return result
    result.policy_version = policy.version
    if not policy.not_before <= now < policy.expires_at:
        return result.model_copy(update={"status": "policy-expired", "reasons": ["POLICY_EXPIRED"]})
    if now >= policy.revocations_valid_until:
        return result.model_copy(update={"reasons": ["REVOCATION_DATA_STALE"]})
    try:
        from securesystemslib.dsse import Envelope
        from securesystemslib.signer import Key

        if len(canonical(proof.model_dump())) > MAX_PROOF_BYTES:
            raise ValueError("Proof too large")
        if set(proof.envelope) != {"payload", "payloadType", "signatures"}:
            raise ValueError("Unsupported envelope fields")
        envelope = Envelope.from_dict(copy.deepcopy(proof.envelope))
        if envelope.payload_type != PAYLOAD_TYPE or len(envelope.signatures) != 1:
            raise ValueError("Unsupported envelope")
        key_id, signature = next(iter(envelope.signatures.items()))
        key = policy.signers.get(key_id)
        if key is None:
            return result.model_copy(
                update={"status": "invalid", "reasons": ["SIGNER_NOT_ALLOWED"]}
            )
        public = Key.from_dict(key_id, copy.deepcopy(key.public_key))
        envelope.verify([public], 1)
        statement = strict_json(envelope.payload)
        predicate = statement["predicate"]
        if (
            set(statement) != {"_type", "subject", "predicateType", "predicate"}
            or statement["_type"] != STATEMENT_TYPE
            or statement["predicateType"] != PREDICATE_TYPE
            or predicate["binding"] != expected
            or statement["subject"] != expected["subjects"]
            or set(predicate)
            != {
                "binding",
                "purpose",
                "issuer",
                "subject",
                "policy_version",
                "issued_at",
                "expires_at",
            }
        ):
            return result.model_copy(update={"status": "invalid", "reasons": ["TARGET_MISMATCH"]})
        if (predicate["purpose"], predicate["issuer"], predicate["subject"]) != (
            key.purpose,
            key.issuer,
            key.subject,
        ) or predicate["policy_version"] not in policy.accepted_claim_versions:
            return result.model_copy(update={"status": "invalid", "reasons": ["CLAIM_NOT_ALLOWED"]})
        result.purpose, result.issuer, result.subject = key.purpose, key.issuer, key.subject
        issued, expires = _time(predicate["issued_at"]), _time(predicate["expires_at"])
        result.claimed_at = issued
        if key.revoked:
            return result.model_copy(update={"status": "revoked", "reasons": ["SIGNER_REVOKED"]})
        if issued > now + timedelta(minutes=5) or expires <= now or expires <= issued:
            return result.model_copy(
                update={"status": "invalid", "reasons": ["CLAIM_EXPIRED_OR_FUTURE"]}
            )
        if expires - issued > timedelta(days=1):
            raise ValueError("Claim lifetime exceeds policy")
        if not proof.timestamp:
            return result.model_copy(update={"reasons": ["TIME_EVIDENCE_MISSING"]})
        trusted = _timestamp(
            bytes.fromhex(signature.signature), proof.timestamp, policy.tsa_roots_pem
        )
        result.trusted_at = trusted
        if (
            trusted > now + timedelta(minutes=5)
            or abs((trusted - issued).total_seconds()) > 300
            or not key.not_before <= trusted < key.not_after
        ):
            raise ValueError("Untrusted signing time")
        return result.model_copy(update={"status": "valid", "reasons": []})
    except ImportError:
        return result.model_copy(update={"reasons": ["VERIFIER_UNAVAILABLE"]})
    except Exception:
        # External parser/crypto errors can contain payloads and internal paths.
        return result.model_copy(update={"status": "invalid", "reasons": ["PROOF_INVALID"]})
