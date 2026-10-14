from uuid import uuid4

import pytest

from app.core.exceptions import RepositoryTooLargeError, UnsafeRepositoryError
from app.utils.filesystem import check_repository_size, terraform_files
from app.utils.terraform import validate_local_module_paths
from app.workspace.manager import WorkspaceManager


def test_workspace_cleanup_on_exception(tmp_path):
    manager = WorkspaceManager(tmp_path / "root")
    scan_id = uuid4()
    with pytest.raises(RuntimeError):
        with manager.create(scan_id) as directory:
            assert directory.name == str(scan_id)
            (directory / "file").write_text("x")
            raise RuntimeError
    assert not directory.exists()


def test_keep_and_no_overwrite(tmp_path):
    manager = WorkspaceManager(tmp_path / "root", keep=True)
    scan_id = uuid4()
    with manager.create(scan_id) as directory:
        pass
    assert directory.exists()
    with pytest.raises(FileExistsError):
        with manager.create(scan_id):
            pass


def test_invalid_uuid(tmp_path):
    manager = WorkspaceManager(tmp_path / "root")
    with pytest.raises(ValueError):
        with manager.create("../../outside"):
            pass


def test_workspace_symlink(tmp_path):
    target = tmp_path / "outside"
    target.mkdir()
    root = tmp_path / "root"
    root.symlink_to(target)
    with pytest.raises(UnsafeRepositoryError):
        WorkspaceManager(root)
    manager = WorkspaceManager(tmp_path / "safe")
    scan_id = uuid4()
    (manager.root / str(scan_id)).symlink_to(target)
    with pytest.raises(FileExistsError):
        with manager.create(scan_id):
            pass
    assert target.exists()


def test_file_discovery_and_size(tmp_path):
    (tmp_path / "main.tf.json").write_text("{}")
    (tmp_path / "vendor").mkdir()
    (tmp_path / "vendor" / "skip.tf").write_text("ignored")
    assert terraform_files(tmp_path) == [tmp_path / "main.tf.json"]
    with pytest.raises(RepositoryTooLargeError):
        check_repository_size(tmp_path, 1)


def test_symlink_rejected(tmp_path):
    (tmp_path / "evil.tf").symlink_to("/etc/passwd")
    with pytest.raises(UnsafeRepositoryError):
        terraform_files(tmp_path)


@pytest.mark.parametrize(
    "source", ["../../outside", "/tmp/outside", "file:///etc", "${var.source}"]
)
def test_module_path_escape(tmp_path, source):
    path = tmp_path / "main.tf"
    path.write_text('module "bad" { source = "' + source + '" }')
    with pytest.raises(UnsafeRepositoryError):
        validate_local_module_paths([path], tmp_path)


def test_local_module_inside_root(tmp_path):
    sub = tmp_path / "modules"
    sub.mkdir()
    path = sub / "main.tf"
    path.write_text('module "ok" { source = "../sibling" }')
    validate_local_module_paths([path], tmp_path)


@pytest.mark.parametrize("source", ["../../outside", "/etc", "file:///tmp/module"])
def test_json_module_path_escape(tmp_path, source):
    import json

    path = tmp_path / "main.tf.json"
    path.write_text(json.dumps({"module": {"bad": {"source": source}}}))
    with pytest.raises(UnsafeRepositoryError):
        validate_local_module_paths([path], tmp_path)


def test_hcl_escaped_absolute_path(tmp_path):
    path = tmp_path / "main.tf"
    path.write_text(r'module "bad" { source = "\u002fetc" }')
    with pytest.raises(UnsafeRepositoryError):
        validate_local_module_paths([path], tmp_path)
