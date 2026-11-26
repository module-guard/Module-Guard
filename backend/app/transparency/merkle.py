"""RFC 9162 section 2.1 Merkle tree over SHA-256: roots, inclusion and consistency proofs.

Everything here works on raw 32-byte hashes. Callers hash their entries with `leaf_hash` and keep
the list; proofs are generated from that list and checked without it. No third-party code.
"""

import hashlib

HASH_SIZE = 32
# 2**64 leaves would need a longer path than any real log; a longer proof is rejected unread.
MAX_PROOF = 64
EMPTY_ROOT = hashlib.sha256(b"").digest()


def leaf_hash(data: bytes) -> bytes:
    return hashlib.sha256(b"\x00" + data).digest()


def node_hash(left: bytes, right: bytes) -> bytes:
    return hashlib.sha256(b"\x01" + left + right).digest()


def _split(n: int) -> int:
    """Largest power of two strictly smaller than n (n >= 2)."""
    return 1 << ((n - 1).bit_length() - 1)


def _mth(hashes: list[bytes], lo: int, hi: int) -> bytes:
    if hi - lo == 1:
        return hashes[lo]
    k = _split(hi - lo)
    return node_hash(_mth(hashes, lo, lo + k), _mth(hashes, lo + k, hi))


def root(hashes: list[bytes], size: int | None = None) -> bytes:
    """Root of the first `size` leaf hashes (all of them by default)."""
    size = len(hashes) if size is None else size
    if not 0 <= size <= len(hashes):
        raise ValueError("tree size out of range")
    return EMPTY_ROOT if size == 0 else _mth(hashes, 0, size)


def inclusion_proof(hashes: list[bytes], index: int, size: int) -> list[bytes]:
    """Audit path for leaf `index` in the tree made of the first `size` leaves."""
    if not 0 <= index < size <= len(hashes):
        raise ValueError("leaf index out of range")

    def path(m: int, lo: int, hi: int) -> list[bytes]:
        if hi - lo == 1:
            return []
        k = _split(hi - lo)
        if m < k:
            return [*path(m, lo, lo + k), _mth(hashes, lo + k, hi)]
        return [*path(m - k, lo + k, hi), _mth(hashes, lo, lo + k)]

    return path(index, 0, size)


def verify_inclusion(
    leaf: bytes, index: int, size: int, proof: list[bytes], expected_root: bytes
) -> bool:
    """RFC 9162 section 2.1.3.2."""
    if not 0 <= index < size or len(proof) > MAX_PROOF:
        return False
    fn, sn, r = index, size - 1, leaf
    for p in proof:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            r = node_hash(p, r)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            r = node_hash(r, p)
        fn >>= 1
        sn >>= 1
    return sn == 0 and r == expected_root


def consistency_proof(hashes: list[bytes], first: int, second: int) -> list[bytes]:
    """Proof that the first `first` leaves are a prefix of the first `second` leaves."""
    if not 0 <= first <= second <= len(hashes):
        raise ValueError("tree sizes out of range")
    if first in (0, second):
        return []  # Trivially consistent: an empty tree, or the same tree.

    def sub(m: int, lo: int, hi: int, complete: bool) -> list[bytes]:
        if m == hi - lo:
            return [] if complete else [_mth(hashes, lo, hi)]
        k = _split(hi - lo)
        if m <= k:
            return [*sub(m, lo, lo + k, complete), _mth(hashes, lo + k, hi)]
        return [*sub(m - k, lo + k, hi, False), _mth(hashes, lo, lo + k)]

    return sub(first, 0, second, True)


def verify_consistency(
    first: int, second: int, first_root: bytes, second_root: bytes, proof: list[bytes]
) -> bool:
    """RFC 9162 section 2.1.4.2, plus the sizes the RFC leaves to the caller (0 and equal)."""
    if first < 0 or first > second or len(proof) > MAX_PROOF:
        return False
    if first == 0:
        return not proof and first_root == EMPTY_ROOT
    if first == second:
        return not proof and first_root == second_root
    path = list(proof)
    if first & (first - 1) == 0:
        path.insert(0, first_root)
    if not path:
        return False
    fn, sn = first - 1, second - 1
    while fn & 1:
        fn >>= 1
        sn >>= 1
    fr = sr = path[0]
    for c in path[1:]:
        if sn == 0:
            return False
        if fn & 1 or fn == sn:
            fr, sr = node_hash(c, fr), node_hash(c, sr)
            if not fn & 1:
                while not fn & 1 and fn != 0:
                    fn >>= 1
                    sn >>= 1
        else:
            sr = node_hash(sr, c)
        fn >>= 1
        sn >>= 1
    return sn == 0 and fr == first_root and sr == second_root
