import io
import os
import stat
import struct
import zipfile
from pathlib import Path

import pytest

from app.core.exceptions import UnsafeRepositoryError, UploadRejectedError, UploadTooLargeError
from app.uploads.archive import ArchiveLimits, extract_zip
from app.uploads.policy import Decision, classify, filter_tree

TF = 'resource "null_resource" "a" {}\n'
LIMITS = ArchiveLimits()


def make_zip(path, entries, compression=zipfile.ZIP_DEFLATED):
    """entries: name -> content, or (ZipInfo, content) for crafted headers."""
    with zipfile.ZipFile(path, "w", compression) as archive:
        for name, content in entries.items():
            archive.writestr(name, content)
    return path


def crafted(path, items):
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as archive:
        for info, content in items:
            archive.writestr(info, content)
    return path


def run(tmp_path, entries=None, limits=LIMITS, archive=None):
    archive = archive or make_zip(tmp_path / "upload.zip", entries)
    return extract_zip(archive, tmp_path / "out", limits)


def rejected(tmp_path, code, **kwargs):
    with pytest.raises(UploadRejectedError) as caught:
        run(tmp_path, **kwargs)
    assert caught.value.code == code, caught.value.code
    # A rejected upload leaves nothing on disk, inside or outside the destination.
    assert not (tmp_path / "out").exists()
    return caught.value


def tree(root):
    return sorted(p.relative_to(root).as_posix() for p in root.rglob("*") if p.is_file())


def test_valid_archive_keeps_allowed_files_and_reports_the_rest(tmp_path):
    report = run(
        tmp_path,
        {
            "main.tf": TF,
            "modules/net/main.tf": TF,
            "variables.tf.json": "{}",
            "templates/init.tftpl": "x",
            "README.md": "# hi",
            "LICENSE": "Apache License Version 2.0",
            "examples/basic/terraform.tfvars.example": 'a = "b"',
            "scripts/setup.sh": "#!/bin/sh",
            "templates/user_data.tpl": "x",
            "lambda/handler.py": "x",
            "bin/tool.exe": b"MZ",
            "docs/diagram.png": b"\x89PNG",
            ".github/workflows/ci.yml": "on: push",
            ".gitignore": ".terraform",
            ".env.example": "TOKEN=",
            "nested.zip": b"PK\x03\x04",
        },
    )
    assert tree(tmp_path / "out") == report.included
    assert report.included == [
        ".github/workflows/ci.yml",
        "LICENSE",
        "README.md",
        "examples/basic/terraform.tfvars.example",
        "lambda/handler.py",
        "main.tf",
        "modules/net/main.tf",
        "scripts/setup.sh",
        "templates/init.tftpl",
        "templates/user_data.tpl",
        "variables.tf.json",
    ]
    excluded = {e["path"]: e["reason"] for e in report.excluded}
    assert set(excluded) == {
        "bin/tool.exe",
        "docs/diagram.png",
        ".gitignore",
        ".env.example",
        "nested.zip",
    }
    assert all(excluded.values())
    assert report.size_bytes == sum(
        p.stat().st_size for p in (tmp_path / "out").rglob("*") if p.is_file()
    )
    assert all(
        stat.S_IMODE(p.stat().st_mode) == 0o644
        for p in (tmp_path / "out").rglob("*")
        if p.is_file()
    )


def test_single_top_level_folder_is_unwrapped(tmp_path):
    report = run(tmp_path, {"repo-main/main.tf": TF, "repo-main/modules/a/main.tf": TF})
    assert report.included == ["main.tf", "modules/a/main.tf"]
    assert (tmp_path / "out" / "main.tf").read_text() == TF


def test_two_top_level_folders_are_kept(tmp_path):
    assert run(tmp_path, {"a/main.tf": TF, "b/main.tf": TF}).included == ["a/main.tf", "b/main.tf"]


@pytest.mark.parametrize(
    "name",
    [
        "../evil.tf",
        "a/../../evil.tf",
        "a/b/../../../evil.tf",
        "..",
        "/etc/cron.d/evil.tf",
        "/abs.tf",
        "..\\evil.tf",
        "a\\b.tf",
        "C:\\Windows\\evil.tf",
        "C:/evil.tf",
        "c:evil.tf",
        "bad\nname.tf",
    ],
)
def test_escaping_or_malformed_paths_reject_the_upload(tmp_path, name):
    error = rejected(tmp_path, "UnsafePath", entries={"main.tf": TF, name: TF})
    assert error.details and error.details[0]["reason"]
    # Nothing was written anywhere near the workspace.
    assert tree(tmp_path) == ["upload.zip"]
    assert not Path("/tmp/evil.tf").exists()


def test_nul_in_a_name_cannot_smuggle_an_extension(tmp_path):
    # zipfile cuts names at NUL, so "evil.exe\0.tf" is judged as "evil.exe": never stored as .tf.
    report = run(tmp_path, {"main.tf": TF, "evil.exe\x00.tf": "x"})
    assert report.included == ["main.tf"] and tree(tmp_path / "out") == ["main.tf"]


def test_overlong_and_overdeep_paths_are_rejected(tmp_path):
    rejected(tmp_path, "UnsafePath", entries={"main.tf": TF, "a/" * 21 + "x.tf": TF})
    rejected(tmp_path, "UnsafePath", entries={"main.tf": TF, "a" * 300 + ".tf": TF})


def symlink_entry(name, target):
    info = zipfile.ZipInfo(name)
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    return info, target


def test_symbolic_link_entries_are_rejected(tmp_path):
    for target in ("/etc/passwd", "../../outside", "main.tf"):
        archive = crafted(
            tmp_path / "upload.zip",
            [(zipfile.ZipInfo("main.tf"), TF), symlink_entry("link.tf", target)],
        )
        rejected(tmp_path, "SymbolicLink", archive=archive)


def test_special_file_entries_are_rejected(tmp_path):
    info = zipfile.ZipInfo("pipe.tf")
    info.create_system = 3
    info.external_attr = (stat.S_IFIFO | 0o644) << 16
    archive = crafted(tmp_path / "upload.zip", [(zipfile.ZipInfo("main.tf"), TF), (info, "")])
    rejected(tmp_path, "SpecialFile", archive=archive)


def test_encrypted_archives_are_rejected(tmp_path):
    archive = make_zip(tmp_path / "upload.zip", {"main.tf": TF})
    data = bytearray(archive.read_bytes())
    # Set the "encrypted" general-purpose bit in the local and central headers.
    for signature, offset in ((b"PK\x03\x04", 6), (b"PK\x01\x02", 8)):
        position = data.index(signature)
        data[position + offset] |= 0x01
    archive.write_bytes(bytes(data))
    rejected(tmp_path, "EncryptedArchive", archive=archive)


@pytest.mark.parametrize(
    "names",
    [
        ("main.tf", "MAIN.TF"),
        ("modules/Net/a.tf", "modules/net/a.tf"),
        ("a.tf", "./a.tf"),
        ("dir//a.tf", "dir/a.tf"),
        ("caf\u00e9.tf", "cafe\u0301.tf"),
    ],
)
def test_names_that_collide_on_disk_are_rejected(tmp_path, names):
    archive = crafted(tmp_path / "upload.zip", [(zipfile.ZipInfo(n), TF) for n in names])
    rejected(tmp_path, "DuplicateName", archive=archive)


def test_nested_archives_are_never_unpacked(tmp_path):
    inner = io.BytesIO()
    with zipfile.ZipFile(inner, "w") as nested:
        nested.writestr("../../escape.tf", TF)
        nested.writestr("secret.pem", "key")
    report = run(tmp_path, {"main.tf": TF, "vendor/bundle.zip": inner.getvalue()})
    assert report.included == ["main.tf"]
    assert [e["path"] for e in report.excluded] == ["vendor/bundle.zip"]
    assert tree(tmp_path / "out") == ["main.tf"]


def test_too_many_files_are_rejected(tmp_path):
    entries = {f"f{i}.tf": TF for i in range(1001)}
    rejected(tmp_path, "TooManyFiles", entries=entries)
    assert len(run(tmp_path, {f"f{i}.tf": TF for i in range(1000)}).included) == 1000


def test_too_many_entries_are_rejected_before_inspection(tmp_path):
    entries = {f"d{i}/": "" for i in range(40)} | {"main.tf": TF}
    rejected(tmp_path, "TooManyFiles", entries=entries, limits=ArchiveLimits(max_entries=30))


def test_declared_size_over_the_limit_is_rejected(tmp_path):
    with pytest.raises(UploadTooLargeError) as caught:
        run(
            tmp_path,
            {"main.tf": TF, "big.json": b"0" * 4096},
            limits=ArchiveLimits(max_uncompressed_bytes=2048),
        )
    assert caught.value.code == "UncompressedTooLarge" and caught.value.status_code == 413
    assert not (tmp_path / "out").exists()


def lie_about_size(archive, name, claimed):
    """Rewrite the uncompressed-size fields of one entry so the headers understate it."""
    data = bytearray(archive.read_bytes())
    encoded = name.encode()
    for signature, size_offset, name_offset in ((b"PK\x03\x04", 22, 30), (b"PK\x01\x02", 24, 46)):
        start = 0
        while (position := data.find(signature, start)) != -1:
            if data[position + name_offset : position + name_offset + len(encoded)] == encoded:
                struct.pack_into("<I", data, position + size_offset, claimed)
            start = position + 4
    archive.write_bytes(bytes(data))
    return archive


def test_headers_that_understate_the_size_do_not_bypass_the_limit(tmp_path):
    # 8 MiB of zeros claims to be 10 bytes: the declared total passes, the real data must not.
    archive = make_zip(tmp_path / "upload.zip", {"main.tf": TF, "bomb.json": b"\0" * (8 * 1024**2)})
    lie_about_size(archive, "bomb.json", 10)
    limits = ArchiveLimits(max_uncompressed_bytes=1024**2)
    with pytest.raises(UploadRejectedError) as caught:
        extract_zip(archive, tmp_path / "out", limits)
    assert caught.value.code in {"InvalidArchive", "UncompressedTooLarge", "CompressionBomb"}
    assert not (tmp_path / "out").exists()


def test_compression_bomb_is_rejected_by_ratio(tmp_path):
    # Honest headers, well under the size limit, but 16 MiB of zeros in ~16 KiB.
    rejected(
        tmp_path, "CompressionBomb", entries={"main.tf": TF, "bomb.json": b"\0" * (16 * 1024**2)}
    )


def test_bomb_hidden_in_an_excluded_file_is_never_decompressed(tmp_path):
    report = run(tmp_path, {"main.tf": TF, "bomb.bin": b"\0" * (16 * 1024**2)})
    assert report.included == ["main.tf"] and report.size_bytes == len(TF)


def test_real_text_is_not_mistaken_for_a_bomb(tmp_path):
    body = "".join(
        f'variable "v{i}" {{\n  type = string\n  default = "{i}"\n}}\n' for i in range(40000)
    )
    assert len(body) > 2 * 1024**2
    assert run(tmp_path, {"main.tf": body}).size_bytes == len(body)


@pytest.mark.parametrize(
    "name",
    [
        "terraform.tfstate",
        "env/prod/terraform.tfstate.backup",
        "prod.tfvars",
        "prod.auto.tfvars.json",
        ".terraform/providers/x",
        "modules/a/.terraform/modules/modules.json",
        ".env",
        "config/.env.production",
        "certs/server.pem",
        "tls.key",
        "id_rsa",
        "keys/id_rsa.pub",
        "client.p12",
    ],
)
def test_sensitive_files_reject_the_whole_upload(tmp_path, name):
    error = rejected(tmp_path, "SensitiveFiles", entries={"main.tf": TF, name: "x"})
    assert [d["path"] for d in error.details] == [name] and error.details[0]["reason"]


def test_all_sensitive_files_are_listed_together(tmp_path):
    error = rejected(
        tmp_path,
        "SensitiveFiles",
        entries={"repo/main.tf": TF, "repo/a.tfvars": "x", "repo/terraform.tfstate": "{}"},
    )
    assert sorted(d["path"] for d in error.details) == ["a.tfvars", "terraform.tfstate"]


@pytest.mark.parametrize(
    "content",
    [b"", b"not a zip at all", b"PK\x03\x04" + b"\0" * 40, b"PK\x05\x06" + b"\0" * 18],
)
def test_non_archives_and_empty_archives_are_rejected(tmp_path, content):
    archive = tmp_path / "upload.zip"
    archive.write_bytes(content)
    with pytest.raises(UploadRejectedError) as caught:
        extract_zip(archive, tmp_path / "out", LIMITS)
    assert caught.value.code in {"InvalidArchive", "EmptyArchive"}


def test_corrupted_member_data_is_rejected(tmp_path):
    archive = make_zip(tmp_path / "upload.zip", {"main.tf": TF * 200})
    data = bytearray(archive.read_bytes())
    data[40] ^= 0xFF
    data[41] ^= 0xFF
    archive.write_bytes(bytes(data))
    rejected(tmp_path, "InvalidArchive", archive=archive)


def test_archive_without_terraform_is_rejected(tmp_path):
    error = rejected(tmp_path, "NoTerraformFiles", entries={"README.md": "x", "run.exe": "x"})
    assert [d["path"] for d in error.details] == ["run.exe"]


def test_directory_only_archive_is_empty(tmp_path):
    rejected(tmp_path, "EmptyArchive", entries={"a/": "", "a/b/": ""})


def test_existing_destination_is_never_overwritten(tmp_path):
    (tmp_path / "out").mkdir()
    (tmp_path / "out" / "keep.txt").write_text("mine")
    with pytest.raises(FileExistsError):
        extract_zip(make_zip(tmp_path / "upload.zip", {"main.tf": TF}), tmp_path / "out", LIMITS)


def test_classification_rules():
    from pathlib import PurePosixPath as P

    assert classify(P("main.tf"))[0] == Decision.INCLUDE
    assert classify(P("LICENSE.txt"))[0] == Decision.INCLUDE
    assert classify(P("a/example.tfvars.example"))[0] == Decision.INCLUDE
    assert classify(P("a/example.tfvars.json"))[0] == Decision.SENSITIVE
    assert classify(P(".env.sample"))[0] == Decision.EXCLUDE
    assert classify(P("Makefile"))[0] == Decision.EXCLUDE
    assert classify(P("x.TFSTATE"))[0] == Decision.SENSITIVE
    assert classify(P("key.tf"))[0] == Decision.INCLUDE


def test_filter_tree_applies_the_same_rules_to_a_cloned_repository(tmp_path):
    for name, content in {
        "main.tf": TF,
        "scripts/a.bin": "x",
        ".git/config": "[core]",
        ".git/objects/secret.pem": "x",
        "README.md": "# x",
    }.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content)
    report = filter_tree(tmp_path)
    assert report.included == ["README.md", "main.tf"]
    assert [e["path"] for e in report.excluded] == ["scripts/a.bin"]
    assert not (tmp_path / "scripts" / "a.bin").exists() and (tmp_path / ".git" / "config").exists()
    (tmp_path / "prod.tfvars").write_text("x")
    with pytest.raises(UploadRejectedError) as caught:
        filter_tree(tmp_path)
    assert caught.value.code == "SensitiveFiles"
    (tmp_path / "prod.tfvars").unlink()
    os.symlink("/etc/passwd", tmp_path / "link.tf")
    with pytest.raises(UnsafeRepositoryError):
        filter_tree(tmp_path)


def test_stored_method_and_unsupported_methods(tmp_path):
    assert run(
        tmp_path,
        {"main.tf": TF},
        archive=make_zip(tmp_path / "s.zip", {"main.tf": TF}, zipfile.ZIP_STORED),
    ).included == ["main.tf"]
    archive = make_zip(tmp_path / "upload.zip", {"main.tf": TF * 50})
    data = bytearray(archive.read_bytes())
    for signature, offset in ((b"PK\x03\x04", 8), (b"PK\x01\x02", 10)):
        struct.pack_into("<H", data, data.index(signature) + offset, 99)
    archive.write_bytes(bytes(data))
    with pytest.raises(UploadRejectedError):
        extract_zip(archive, tmp_path / "out2", LIMITS)
