"""Real local DSSE signatures and OpenSSL TSA responses; no cloud or public log."""

import base64
import shutil
from datetime import UTC, datetime, timedelta

import pytest


def crypto_material(tmp_path_factory):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes, serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    from cryptography.x509.oid import ExtendedKeyUsageOID, NameOID
    from rfc3161_client import HashAlgorithm, TimestampRequestBuilder
    from securesystemslib.signer import CryptoSigner

    from app.schemas.attestation import AttestationPolicy, TrustedSigner
    from app.utils.subprocess import ProcessRunner, isolated_environment

    openssl = shutil.which("openssl")
    if not openssl:
        pytest.skip("OpenSSL is needed for real RFC 3161 test responses")
    root_dir = tmp_path_factory.mktemp("attestation-tsa")
    now = datetime.now(UTC).replace(microsecond=0)
    ca_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    tsa_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    ca_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test only CA")])
    tsa_name = x509.Name([x509.NameAttribute(NameOID.COMMON_NAME, "Test only TSA")])

    def cert(subject, public, ca):
        return (
            x509.CertificateBuilder()
            .subject_name(subject)
            .issuer_name(ca_name)
            .public_key(public)
            .serial_number(x509.random_serial_number())
            .not_valid_before(now - timedelta(days=1))
            .not_valid_after(now + timedelta(days=7))
            .add_extension(x509.BasicConstraints(ca=ca, path_length=None), critical=True)
            .add_extension(
                x509.KeyUsage(
                    digital_signature=True,
                    content_commitment=False,
                    key_encipherment=False,
                    data_encipherment=False,
                    key_agreement=False,
                    key_cert_sign=ca,
                    crl_sign=ca,
                    encipher_only=None,
                    decipher_only=None,
                ),
                critical=True,
            )
        )

    ca = cert(ca_name, ca_key.public_key(), True).sign(ca_key, hashes.SHA256())
    tsa = (
        cert(tsa_name, tsa_key.public_key(), False)
        .add_extension(x509.ExtendedKeyUsage([ExtendedKeyUsageOID.TIME_STAMPING]), critical=True)
        .sign(ca_key, hashes.SHA256())
    )
    root_pem = ca.public_bytes(serialization.Encoding.PEM).decode()
    (root_dir / "root.pem").write_text(root_pem)
    (root_dir / "tsa.pem").write_bytes(tsa.public_bytes(serialization.Encoding.PEM))
    (root_dir / "tsa.key").write_bytes(
        tsa_key.private_bytes(
            serialization.Encoding.PEM,
            serialization.PrivateFormat.PKCS8,
            serialization.NoEncryption(),
        )
    )
    (root_dir / "tsa.key").chmod(0o600)
    (root_dir / "serial").write_text("01")
    (root_dir / "tsa.cnf").write_text("""[tsa]
serial = serial
signer_cert = tsa.pem
signer_key = tsa.key
signer_digest = sha256
default_policy = 1.3.6.1.4.1.55555.1
digests = sha256
accuracy = secs:1
ordering = no
tsa_name = yes
ess_cert_id_chain = no
ess_cert_id_alg = sha256
""")

    def timestamp(signature, *, request_bytes=None):
        request = (
            TimestampRequestBuilder().data(signature).hash_algorithm(HashAlgorithm.SHA256).build()
        )
        (root_dir / "request.der").write_bytes(request_bytes or request.as_bytes())
        result = ProcessRunner().run(
            [
                openssl,
                "ts",
                "-reply",
                "-config",
                "tsa.cnf",
                "-section",
                "tsa",
                "-queryfile",
                "request.der",
                "-out",
                "response.der",
            ],
            cwd=root_dir,
            env=isolated_environment(root_dir),
            timeout=10,
            output_limit=16384,
        )
        assert result.returncode == 0, "Test TSA generation failed"
        return base64.b64encode((root_dir / "response.der").read_bytes()).decode()

    signer = CryptoSigner.generate_ecdsa()
    key_id = signer.public_key.keyid
    policy = AttestationPolicy(
        version="test-policy/1",
        accepted_claim_versions=["test-policy/1"],
        not_before=now - timedelta(days=1),
        expires_at=now + timedelta(days=2),
        revocations_valid_until=now + timedelta(days=1),
        signers={
            key_id: TrustedSigner(
                public_key=signer.public_key.to_dict(),
                issuer="test-platform",
                subject="test-worker",
                purpose="platform",
                not_before=now - timedelta(days=1),
                not_after=now + timedelta(days=2),
            )
        },
        tsa_roots_pem=[root_pem],
        tsa_url="https://tsa.invalid",
        signing_key_id=key_id,
    )
    return signer, timestamp, policy
