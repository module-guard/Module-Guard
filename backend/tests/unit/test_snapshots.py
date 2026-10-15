import os
import tarfile

import pytest

from app.core.exceptions import RepositoryTooLargeError, UnsafeRepositoryError
from app.storage.snapshots import SnapshotStore


def make_tree(root, files):
    for name, content in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    return root


FILES = {
    "main.tf": 'resource "null_resource" "a" {}\n',
    "modules/net/main.tf": "# child\n",
    "templates/user_data.sh": "#!/bin/sh\n",
    "LICENSE": "Apache License Version 2.0",
    "example.tfvars.example": 'name = "x"\n',
}


def names(store, sha256):
    with tarfile.open(store.path(sha256)) as archive:
        return archive.getnames()


def test_same_content_same_digest_and_single_file(tmp_path):
    store = SnapshotStore(tmp_path / "store", 1024**2)
    first = store.save(make_tree(tmp_path / "a", FILES))
    second_tree = make_tree(tmp_path / "b", dict(reversed(FILES.items())))
    os.utime(second_tree / "main.tf", (1, 1))  # Timestamps must not affect the digest.
    second = store.save(second_tree)
    assert first == second and first.file_count == len(FILES)
    assert [p.name for p in (tmp_path / "store").iterdir()] == [f"{first.sha256}.tar.gz"]
    assert first.size_bytes == store.path(first.sha256).stat().st_size
    changed = store.save(make_tree(tmp_path / "c", {**FILES, "main.tf": "# changed\n"}))
    assert changed.sha256 != first.sha256


def test_archive_is_relative_sorted_and_normalised(tmp_path):
    store = SnapshotStore(tmp_path / "store", 1024**2)
    tree = make_tree(tmp_path / "a", FILES)
    (tree / "templates/user_data.sh").chmod(0o700)
    info = store.save(tree)
    with tarfile.open(store.path(info.sha256)) as archive:
        members = archive.getmembers()
    assert [m.name for m in members] == sorted(FILES)
    assert all(not m.name.startswith(("/", "..")) and m.isfile() for m in members)
    assert {(m.mtime, m.uid, m.gid) for m in members} == {(0, 0, 0)}
    modes = {m.name: m.mode for m in members}
    assert modes["templates/user_data.sh"] == 0o755 and modes["main.tf"] == 0o644


def test_excludes_vcs_state_and_variable_files(tmp_path):
    store = SnapshotStore(tmp_path / "store", 1024**2)
    tree = make_tree(
        tmp_path / "a",
        {
            **FILES,
            ".git/config": "[core]",
            ".terraform/providers/x": "binary",
            "terraform.tfstate": "{}",
            "terraform.tfstate.backup": "{}",
            "prod.tfvars": 'password = "secret"',
            "prod.auto.tfvars.json": "{}",
        },
    )
    assert names(store, store.save(tree).sha256) == sorted(FILES)


@pytest.mark.parametrize(
    "name",
    [
        ".env",
        ".env.local",
        ".env.example",
        "config/.env.production",
        "server.pem",
        "certs/CA.PEM",
        "tls.key",
        "id_rsa",
        "id_rsa.pub",
        "keys/id_rsa_deploy",
        "client.p12",
    ],
)
def test_excludes_secret_files(tmp_path, name):
    store = SnapshotStore(tmp_path / "store", 1024**2)
    tree = make_tree(tmp_path / "a", {**FILES, name: "secret"})
    info = store.save(tree)
    assert names(store, info.sha256) == sorted(FILES) and info.file_count == len(FILES)
    # Excluded files do not affect the digest either.
    assert info == store.save(make_tree(tmp_path / "b", FILES))


@pytest.mark.parametrize("name", ["environment.tf", "keys.tf", "pem.md", "modules/key/main.tf"])
def test_keeps_lookalike_names(tmp_path, name):
    store = SnapshotStore(tmp_path / "store", 1024**2)
    info = store.save(make_tree(tmp_path / "a", {**FILES, name: "# kept"}))
    assert name in names(store, info.sha256)


def test_rejects_symlinks_and_oversized_trees(tmp_path):
    store = SnapshotStore(tmp_path / "store", 10)
    tree = make_tree(tmp_path / "a", {"main.tf": "x" * 11})
    with pytest.raises(RepositoryTooLargeError):
        store.save(tree)
    linked = make_tree(tmp_path / "b", {"main.tf": "x"})
    (linked / "link.tf").symlink_to("/etc/passwd")
    with pytest.raises(UnsafeRepositoryError):
        store.save(linked)
    # Failed saves leave neither archives nor temporary files behind.
    assert not (tmp_path / "store").exists() or not list((tmp_path / "store").iterdir())


def test_path_rejects_non_digest_names(tmp_path):
    store = SnapshotStore(tmp_path, 1)
    with pytest.raises(ValueError):
        store.path("../../etc/passwd")
