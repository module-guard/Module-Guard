import hashlib

import pytest

from app.storage.integrity import (
    IntegrityError,
    digest,
    manifest_bytes,
    manifest_for,
    read_tree,
    verify_tree,
)
from app.storage.snapshots import SnapshotStore


def test_manifest_exact_binary_deterministic(tmp_path):
    (tmp_path / "z.bin").write_bytes(b"\x00\xff\x01")
    (tmp_path / "main.tf").write_bytes(b"abc")
    files = read_tree(tmp_path, 1000)
    manifest = manifest_for(files)
    assert [f["path"] for f in manifest["files"]] == ["main.tf", "z.bin"]
    assert manifest["files"][0]["sha256"] == hashlib.sha256(b"abc").hexdigest()
    assert manifest_bytes(manifest) == manifest_bytes(
        manifest_for(dict(reversed(list(files.items()))))
    )
    verify_tree(tmp_path, manifest, 1000, digest(manifest_bytes(manifest)))


@pytest.mark.parametrize("change", ["edit", "add", "delete", "rename", "mode"])
def test_tree_mutation(tmp_path, change):
    source = tmp_path / "main.tf"
    source.write_bytes(b"abc")
    manifest = manifest_for(read_tree(tmp_path, 1000))
    if change == "edit":
        source.write_bytes(b"abd")
    elif change == "add":
        (tmp_path / "new.tf").write_bytes(b"")
    elif change == "delete":
        source.unlink()
    elif change == "mode":
        source.chmod(0o755)
    else:
        source.rename(tmp_path / "renamed.tf")
    with pytest.raises(IntegrityError) as error:
        verify_tree(tmp_path, manifest, 1000)
    assert error.value.status == "MISMATCH"
    assert error.value.details


def test_symlinks_and_limit_refused(tmp_path):
    (tmp_path / "link").symlink_to(tmp_path.parent, target_is_directory=True)
    with pytest.raises(IntegrityError):
        read_tree(tmp_path, 1000)
    (tmp_path / "link").unlink()
    (tmp_path / "large").write_bytes(b"123")
    with pytest.raises(IntegrityError):
        read_tree(tmp_path, 2)


def test_snapshot_verify_pins_actual_bytes_and_refuses_overwrite(tmp_path):
    source = tmp_path / "src"
    source.mkdir()
    (source / "main.tf").write_bytes(b"abc")
    store = SnapshotStore(tmp_path / "snapshots", 1000)
    info = store.save(source)
    data = store.verified_bytes(info.sha256, info.manifest, info.manifest_sha256)
    assert digest(data) == info.sha256
    store.path(info.sha256).write_bytes(b"corrupt")
    assert digest(data) == info.sha256  # Already verified bytes do not change with the pathname.
    with pytest.raises(IntegrityError):
        store.verified_bytes(info.sha256, info.manifest, info.manifest_sha256)
    with pytest.raises(IntegrityError):
        store.save(source)
    assert store.path(info.sha256).read_bytes() == b"corrupt"


def test_collection_changed_between_reads(tmp_path, monkeypatch):
    from app.storage import snapshots

    source = tmp_path / "src"
    source.mkdir()
    file = source / "main.tf"
    file.write_bytes(b"abc")
    original = snapshots.read_tree
    calls = 0

    def changing(*args):
        nonlocal calls
        result = original(*args)
        calls += 1
        if calls == 1:
            file.write_bytes(b"abd")
        return result

    monkeypatch.setattr(snapshots, "read_tree", changing)
    with pytest.raises(IntegrityError):
        SnapshotStore(tmp_path / "snapshots", 1000).save(source)


@pytest.mark.parametrize("name", ["../outside.tf", "/outside.tf", "a\\b.tf"])
def test_unsafe_tar_paths_fail_closed(tmp_path, name):
    import io
    import tarfile

    content = io.BytesIO()
    with tarfile.open(fileobj=content, mode="w:gz") as tar:
        member = tarfile.TarInfo(name)
        member.size = 3
        tar.addfile(member, io.BytesIO(b"abc"))
    data = content.getvalue()
    store = SnapshotStore(tmp_path, 1000)
    sha = digest(data)
    store.path(sha).write_bytes(data)
    manifest = manifest_for({"main.tf": (b"abc", 0o644)})
    with pytest.raises(IntegrityError):
        store.verified_bytes(sha, manifest, digest(manifest_bytes(manifest)))
