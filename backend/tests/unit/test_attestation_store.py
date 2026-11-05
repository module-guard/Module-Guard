from uuid import uuid4

import pytest

from app.attestations.signing import AttestationUnavailable
from app.attestations.store import MAX_ENTRIES, ProofStore
from app.core.exceptions import ConflictError
from app.schemas.attestation import AttestationProof


def test_atomic_append_private_permissions_and_version_isolation(tmp_path):
    store = ProofStore(tmp_path / "proofs")
    first, second = uuid4(), uuid4()
    proof = AttestationProof(envelope={"test": True})
    assert store.read(first) == []
    with store.issuance(first) as fd:
        store.append(fd, proof)
        with pytest.raises(ConflictError):
            with store.issuance(first):
                pass
    assert len(store.read(first)) == 1 and store.read(second) == []
    for file in (store.root / str(first)).glob("*.json"):
        assert file.stat().st_mode & 0o777 == 0o600
    with store.issuance(second) as fd:
        store.append(fd, proof)
    assert len(store.read(first)) == len(store.read(second)) == 1


def test_version_quota_does_not_block_other_version(tmp_path):
    store = ProofStore(tmp_path / "proofs")
    first, second = uuid4(), uuid4()
    proof = AttestationProof(envelope={})
    for _ in range(MAX_ENTRIES):
        with store.issuance(first) as fd:
            store.append(fd, proof)
    with pytest.raises(ConflictError):
        with store.issuance(first):
            pass
    with store.issuance(second) as fd:
        store.append(fd, proof)
    assert len(store.read(second)) == 1


def test_links_and_corrupt_files_are_not_read(tmp_path):
    store = ProofStore(tmp_path / "proofs")
    version = uuid4()
    with store.issuance(version):
        pass
    outside = tmp_path / "private"
    outside.write_text("SECRET")
    name = store.root / str(version) / f"{'0' * 20}-{uuid4()}.json"
    name.symlink_to(outside)
    with pytest.raises(AttestationUnavailable):
        store.read(version)
    name.unlink()
    name.write_text('{"duplicate":1,"duplicate":2}')
    with pytest.raises(AttestationUnavailable):
        store.read(version)
    linked_version = uuid4()
    (store.root / str(linked_version)).symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(AttestationUnavailable):
        store.read(linked_version)
