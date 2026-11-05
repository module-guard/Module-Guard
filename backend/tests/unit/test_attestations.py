import base64
import copy
from datetime import UTC, datetime, timedelta

import pytest

from app.attestations import signing
from app.schemas.attestation import AttestationProof
from tests.attestation_helpers import crypto_material


@pytest.fixture(scope="module")
def material(tmp_path_factory):
    return crypto_material(tmp_path_factory)


@pytest.fixture
def issued(material):
    signer, timestamp, policy = material
    binding = {
        "subjects": [{"name": "manifest:version-1", "digest": {"sha256": "a" * 64}}],
        "version_id": "version-1",
        "scan_id": "scan-1",
        "lineage_id": "lineage-1",
    }
    proof = signing.issue(binding, policy, signer=signer, timestamp=timestamp)
    return proof, binding, policy


def test_real_signature_and_timestamp(issued):
    proof, binding, policy = issued
    checked = signing.verify(proof, binding, policy)
    assert checked.status == "valid"
    assert checked.purpose == "platform" and checked.subject == "test-worker"
    assert checked.trusted_at and checked.claimed_at
    assert checked.security_guarantee is False
    assert policy.tsa_roots_pem[0] not in proof.model_dump_json()


@pytest.mark.parametrize("field", ["version_id", "scan_id", "lineage_id", "subjects"])
def test_other_target_and_derived_version_are_rejected(issued, field):
    proof, binding, policy = issued
    altered = copy.deepcopy(binding)
    altered[field] = "different"
    assert signing.verify(proof, altered, policy).reasons == ["TARGET_MISMATCH"]


def test_payload_byte_tampering(issued):
    proof, binding, policy = issued
    proof = proof.model_copy(deep=True)
    payload = base64.b64decode(proof.envelope["payload"])
    proof.envelope["payload"] = base64.b64encode(
        payload.replace(b"version-1", b"version-2")
    ).decode()
    assert signing.verify(proof, binding, policy).status == "invalid"


def test_signature_byte_tampering(issued):
    proof, binding, policy = issued
    proof = proof.model_copy(deep=True)
    signature = bytearray(base64.b64decode(proof.envelope["signatures"][0]["sig"]))
    signature[-1] ^= 1
    proof.envelope["signatures"][0]["sig"] = base64.b64encode(signature).decode()
    assert signing.verify(proof, binding, policy).status == "invalid"


def test_untrusted_key_and_embedded_public_key_do_not_establish_trust(issued):
    from securesystemslib.signer import CryptoSigner

    proof, binding, policy = issued
    changed = policy.model_copy(deep=True)
    changed.signers[
        changed.signing_key_id
    ].public_key = CryptoSigner.generate_ecdsa().public_key.to_dict()
    proof = proof.model_copy(deep=True)
    proof.envelope["public_key"] = policy.signers[policy.signing_key_id].public_key
    assert signing.verify(proof, binding, changed).status == "invalid"
    proof.envelope.pop("public_key")
    proof.envelope["signatures"][0]["keyid"] = "not-allowed"
    assert signing.verify(proof, binding, policy).reasons == ["SIGNER_NOT_ALLOWED"]


@pytest.mark.parametrize(
    "field,value", [("subject", "wrong"), ("issuer", "wrong"), ("purpose", "upstream")]
)
def test_claimed_identity_must_match_trusted_key_policy(issued, field, value):
    proof, binding, policy = issued
    changed = policy.model_copy(deep=True)
    setattr(changed.signers[changed.signing_key_id], field, value)
    assert signing.verify(proof, binding, changed).reasons == ["CLAIM_NOT_ALLOWED"]


def test_revoked_and_expired_policy_and_claim(issued):
    proof, binding, policy = issued
    changed = policy.model_copy(deep=True)
    changed.signers[changed.signing_key_id].revoked = True
    assert signing.verify(proof, binding, changed).status == "revoked"
    changed = policy.model_copy(update={"expires_at": datetime.now(UTC) - timedelta(seconds=1)})
    assert signing.verify(proof, binding, changed).status == "policy-expired"
    later = datetime.now(UTC) + timedelta(days=1, seconds=1)
    changed = policy.model_copy(update={"revocations_valid_until": later + timedelta(days=1)})
    assert signing.verify(proof, binding, changed, later).reasons == ["CLAIM_EXPIRED_OR_FUTURE"]


def test_missing_time_policy_and_stale_revocations_are_unknown(issued):
    proof, binding, policy = issued
    assert signing.verify(
        proof.model_copy(update={"timestamp": None}), binding, policy
    ).reasons == ["TIME_EVIDENCE_MISSING"]
    assert signing.verify(proof, binding, None).status == "unknown"
    changed = policy.model_copy(
        update={"revocations_valid_until": datetime.now(UTC) - timedelta(seconds=1)}
    )
    assert signing.verify(proof, binding, changed).reasons == ["REVOCATION_DATA_STALE"]


def test_other_signature_timestamp_and_untrusted_tsa_fail(issued, material):
    proof, binding, policy = issued
    _, timestamp, _ = material
    other = proof.model_copy(update={"timestamp": timestamp(b"different signature")})
    assert signing.verify(other, binding, policy).status == "invalid"
    changed = policy.model_copy(update={"tsa_roots_pem": []})
    assert signing.verify(proof, binding, changed).status == "invalid"


def test_upstream_is_distinct_from_platform(issued, material):
    from securesystemslib.dsse import Envelope

    proof, binding, policy = issued
    signer, timestamp, _ = material
    statement = signing.strict_json(base64.b64decode(proof.envelope["payload"]))
    statement["predicate"]["purpose"] = "upstream"
    envelope = Envelope(signing.canonical(statement), signing.PAYLOAD_TYPE, {})
    signature = envelope.sign(signer)
    upstream = AttestationProof(
        envelope=envelope.to_dict(), timestamp=timestamp(bytes.fromhex(signature.signature))
    )
    assert signing.verify(upstream, binding, policy).status == "invalid"
    changed = policy.model_copy(deep=True)
    changed.signers[changed.signing_key_id].purpose = "upstream"
    assert signing.verify(upstream, binding, changed).purpose == "upstream"
    assert signing.verify(upstream, binding, changed).status == "valid"
    with pytest.raises(signing.AttestationUnavailable):
        signing.issue(binding, changed, signer=signer, timestamp=timestamp)


def test_duplicate_keys_and_nonfinite_numbers_rejected():
    with pytest.raises(ValueError):
        signing.strict_json(b'{"key":1,"key":2}')
    with pytest.raises(ValueError):
        signing.strict_json(b'{"key":NaN}')


def test_production_kms_and_http_adapters_with_real_crypto(material, monkeypatch):
    import boto3
    import httpx
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.asymmetric import ec, utils

    signer, timestamp, policy = material
    policy = policy.model_copy(deep=True)
    arn = "arn:aws:kms:us-east-1:123456789012:key/01234567-89ab-cdef-0123-456789abcdef"
    policy.signers[policy.signing_key_id].kms_key_arn = arn
    calls = []

    class LocalKMS:
        def sign(self, **kwargs):
            calls.append(kwargs)
            assert kwargs["KeyId"] == arn
            assert kwargs["MessageType"] == "DIGEST"
            assert kwargs["SigningAlgorithm"] == "ECDSA_SHA_256"
            return {
                "Signature": signer._private_key.sign(
                    kwargs["Message"], ec.ECDSA(utils.Prehashed(hashes.SHA256()))
                )
            }

    monkeypatch.setattr(boto3, "client", lambda *args, **kwargs: LocalKMS())
    original_client = httpx.Client

    def handler(request):
        assert str(request.url) == policy.tsa_url
        assert request.headers["content-type"] == "application/timestamp-query"
        return httpx.Response(
            200, content=base64.b64decode(timestamp(b"unused", request_bytes=request.content))
        )

    monkeypatch.setattr(
        httpx,
        "Client",
        lambda **kwargs: original_client(transport=httpx.MockTransport(handler), **kwargs),
    )
    binding = {"subjects": [], "version_id": "test"}
    proof = signing.issue(binding, policy)
    assert signing.verify(proof, binding, policy).status == "valid"
    assert len(calls) == 1 and len(calls[0]["Message"]) == 32


def test_kms_raw_failure_is_not_logged_or_returned(caplog):
    class BrokenKMS:
        def sign(self, **kwargs):
            raise RuntimeError("SECRET_KMS_ERROR")

    with pytest.raises(signing.AttestationUnavailable) as error:
        signing._KMSClient(BrokenKMS()).sign()
    assert "SECRET_KMS_ERROR" not in str(error.value) + caplog.text
