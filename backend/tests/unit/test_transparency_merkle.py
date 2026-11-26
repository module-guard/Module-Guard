"""merkle.py against a separate, deliberately naive implementation of RFC 9162 section 2.1."""

import hashlib

import pytest

from app.transparency import merkle


# ---- reference: explicit tree of nested tuples, plain recursion, no bit tricks ----------------
def h(prefix, *parts):
    return hashlib.sha256(prefix + b"".join(parts)).digest()


def build(leaves):
    """(hash, size, left, right) with the split at the largest power of two below n."""
    if len(leaves) == 1:
        return (h(b"\x00", leaves[0]), 1, None, None)
    k = 1
    while k * 2 < len(leaves):
        k *= 2
    left, right = build(leaves[:k]), build(leaves[k:])
    return (h(b"\x01", left[0], right[0]), len(leaves), left, right)


def ref_root(leaves):
    return hashlib.sha256(b"").digest() if not leaves else build(leaves)[0]


def ref_inclusion(node, index):
    if node[2] is None:
        return []
    left, right = node[2], node[3]
    if index < left[1]:
        return ref_inclusion(left, index) + [right[0]]
    return ref_inclusion(right, index - left[1]) + [left[0]]


def ref_consistency(node, m, complete=True):
    if m == node[1]:
        return [] if complete else [node[0]]
    left, right = node[2], node[3]
    if m <= left[1]:
        return ref_consistency(left, m, complete) + [right[0]]
    return ref_consistency(right, m - left[1], False) + [left[0]]


def ref_verify_inclusion(leaf, m, n, proof):
    """Recompute the root by recursion on (index, size), consuming the proof from the end."""
    if n == 1:
        return leaf if not proof else None
    k = 1
    while k * 2 < n:
        k *= 2
    if not proof:
        return None
    if m < k:
        sub = ref_verify_inclusion(leaf, m, k, proof[:-1])
        return None if sub is None else h(b"\x01", sub, proof[-1])
    sub = ref_verify_inclusion(leaf, m - k, n - k, proof[:-1])
    return None if sub is None else h(b"\x01", proof[-1], sub)


DATA = [f"leaf-{i}".encode() for i in range(64)]
HASHES = [merkle.leaf_hash(d) for d in DATA]


def flip(value: bytes) -> bytes:
    return bytes([value[0] ^ 1]) + value[1:]


def test_known_vectors():
    assert (
        merkle.EMPTY_ROOT.hex()
        == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    assert merkle.root([]) == merkle.EMPTY_ROOT
    # RFC 6962 test vector: the tree of one empty leaf.
    assert (
        merkle.root([merkle.leaf_hash(b"")]).hex()
        == "6e340b9cffb37a989ca544e6bb780a2c78901d3fb33738768511a30617afa01d"
    )


def test_roots_and_inclusion_match_reference_for_every_size_and_leaf():
    for n in range(1, 65):
        tree = build(DATA[:n])
        assert merkle.root(HASHES, n) == tree[0] == ref_root(DATA[:n])
        for i in range(n):
            proof = merkle.inclusion_proof(HASHES, i, n)
            assert proof == ref_inclusion(tree, i)
            assert merkle.verify_inclusion(HASHES[i], i, n, proof, tree[0])
            assert ref_verify_inclusion(HASHES[i], i, n, proof) == tree[0]


def test_consistency_matches_reference_for_every_size_pair():
    for n in range(1, 65):
        tree = build(DATA[:n])
        for m in range(1, n + 1):
            proof = merkle.consistency_proof(HASHES, m, n)
            if m < n:
                assert proof == ref_consistency(tree, m)
            else:
                assert proof == []
            assert merkle.verify_consistency(m, n, ref_root(DATA[:m]), tree[0], proof)


def test_empty_first_tree_and_equal_trees_are_handled_outside_the_rfc_algorithm():
    root = merkle.root(HASHES, 5)
    assert merkle.verify_consistency(0, 5, merkle.EMPTY_ROOT, root, [])
    assert not merkle.verify_consistency(0, 5, root, root, [])
    assert not merkle.verify_consistency(0, 5, merkle.EMPTY_ROOT, root, [root])
    assert merkle.verify_consistency(5, 5, root, root, [])
    assert not merkle.verify_consistency(5, 5, root, flip(root), [])
    assert not merkle.verify_consistency(5, 5, root, root, [root])
    assert not merkle.verify_consistency(6, 5, root, root, [])


def test_any_one_bit_change_fails_inclusion():
    for n in (1, 2, 7, 8, 33, 64):
        root = merkle.root(HASHES, n)
        for i in range(n):
            proof = merkle.inclusion_proof(HASHES, i, n)
            assert not merkle.verify_inclusion(flip(HASHES[i]), i, n, proof, root)
            assert not merkle.verify_inclusion(HASHES[i], i, n, proof, flip(root))
            for position in range(len(proof)):
                changed = [*proof[:position], flip(proof[position]), *proof[position + 1 :]]
                assert not merkle.verify_inclusion(HASHES[i], i, n, changed, root)
            assert not merkle.verify_inclusion(HASHES[i], i, n, proof[:-1], root) or n == 1
            assert not merkle.verify_inclusion(HASHES[i], i, n, [*proof, root], root)
            wrong_index = (i + 1) % n
            if n > 1 and HASHES[i] != HASHES[wrong_index]:
                assert not merkle.verify_inclusion(HASHES[i], wrong_index, n, proof, root)
        assert not merkle.verify_inclusion(HASHES[0], n, n, [], root)
        assert not merkle.verify_inclusion(HASHES[0], -1, n, [], root)


def test_any_one_bit_change_fails_consistency():
    for n in (2, 3, 8, 13, 64):
        second = merkle.root(HASHES, n)
        for m in range(1, n):
            first = merkle.root(HASHES, m)
            proof = merkle.consistency_proof(HASHES, m, n)
            assert merkle.verify_consistency(m, n, first, second, proof)
            assert not merkle.verify_consistency(m, n, flip(first), second, proof)
            assert not merkle.verify_consistency(m, n, first, flip(second), proof)
            assert not merkle.verify_consistency(m, n, first, second, proof[:-1])
            assert not merkle.verify_consistency(m, n, first, second, [*proof, second])
            for position in range(len(proof)):
                changed = [*proof[:position], flip(proof[position]), *proof[position + 1 :]]
                assert not merkle.verify_consistency(m, n, first, second, changed)


def test_a_rewritten_history_is_not_consistent():
    forged = list(HASHES)
    forged[2] = merkle.leaf_hash(b"rewritten")
    old_root = merkle.root(HASHES, 4)
    proof = merkle.consistency_proof(forged, 4, 16)
    assert not merkle.verify_consistency(4, 16, old_root, merkle.root(forged, 16), proof)
    # A log that grew from the genuine prefix is still accepted.
    assert merkle.verify_consistency(
        4, 16, old_root, merkle.root(HASHES, 16), merkle.consistency_proof(HASHES, 4, 16)
    )


def test_proof_length_is_bounded_and_ranges_are_checked():
    root = merkle.root(HASHES, 4)
    long = [root] * (merkle.MAX_PROOF + 1)
    assert not merkle.verify_inclusion(HASHES[0], 0, 4, long, root)
    assert not merkle.verify_consistency(1, 4, HASHES[0], root, long)
    for call in (
        lambda: merkle.inclusion_proof(HASHES, 4, 4),
        lambda: merkle.inclusion_proof(HASHES, 0, 65),
        lambda: merkle.consistency_proof(HASHES, 5, 4),
        lambda: merkle.consistency_proof(HASHES, 1, 65),
        lambda: merkle.root(HASHES, 65),
    ):
        with pytest.raises(ValueError):
            call()
