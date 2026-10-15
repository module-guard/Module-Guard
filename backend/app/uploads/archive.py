"""Validate and unpack an untrusted zip without ever trusting what its headers claim."""

import re
import shutil
import stat
import unicodedata
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from app.core.exceptions import UploadRejectedError, UploadTooLargeError
from app.uploads.policy import (
    Decision,
    SourceReport,
    classify,
    reject_sensitive,
    require_terraform,
)

CHUNK = 64 * 1024
DRIVE = re.compile(r"^[A-Za-z]:")


@dataclass(frozen=True)
class ArchiveLimits:
    max_uncompressed_bytes: int = 50 * 1024**2
    max_files: int = 1000
    max_entries: int = 5000
    max_path_length: int = 255
    max_depth: int = 20
    # Real Terraform text compresses roughly 5-15x; far beyond that is a decompression bomb.
    max_ratio: int = 200
    ratio_floor_bytes: int = 1024**2


def _reject(message: str, code: str, path: str | None = None) -> UploadRejectedError:
    details = [{"path": path, "reason": message}] if path else []
    return UploadRejectedError(message, code, details)


def _safe_parts(name: str) -> tuple[str, ...]:
    """Relative path components of an entry, or a rejection for anything that could escape."""
    if not name or any(ord(c) < 0x20 or ord(c) == 0x7F for c in name):
        raise _reject("파일 이름에 허용되지 않는 문자가 있습니다.", "UnsafePath", repr(name)[:80])
    if "\\" in name:
        raise _reject("역슬래시가 들어간 경로는 허용되지 않습니다.", "UnsafePath", name)
    if name.startswith("/"):
        raise _reject("절대 경로는 허용되지 않습니다.", "UnsafePath", name)
    if DRIVE.match(name):
        raise _reject("드라이브 문자가 들어간 경로는 허용되지 않습니다.", "UnsafePath", name)
    parts = tuple(part for part in name.split("/") if part not in ("", "."))
    if ".." in parts:
        raise _reject("상위 폴더(..)를 가리키는 경로는 허용되지 않습니다.", "UnsafePath", name)
    return parts


def _check_type(info: zipfile.ZipInfo) -> None:
    if info.flag_bits & 0x1:
        raise _reject("암호가 걸린 zip은 올릴 수 없습니다.", "EncryptedArchive", info.filename)
    mode = info.external_attr >> 16
    if stat.S_ISLNK(mode):
        raise _reject("심볼릭 링크는 허용되지 않습니다.", "SymbolicLink", info.filename)
    if stat.S_IFMT(mode) not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise _reject("일반 파일이 아닌 항목은 허용되지 않습니다.", "SpecialFile", info.filename)


def extract_zip(archive_path: Path, destination: Path, limits: ArchiveLimits) -> SourceReport:
    """Unpack the storable files into destination (which must not exist yet).

    Structural attacks reject the whole upload; files outside the allow-list are skipped and
    reported. Nothing is left behind when the upload is rejected.
    """
    try:
        return _extract(archive_path, destination, limits)
    except BaseException:
        shutil.rmtree(destination, ignore_errors=True)
        raise


def _extract(archive_path: Path, destination: Path, limits: ArchiveLimits) -> SourceReport:
    try:
        archive = zipfile.ZipFile(archive_path)
    except (zipfile.BadZipFile, OSError):
        raise _reject("zip 파일이 아니거나 손상되었습니다.", "InvalidArchive") from None
    with archive:
        entries = archive.infolist()
        if len(entries) > limits.max_entries:
            raise _reject("zip 안의 항목이 너무 많습니다.", "TooManyFiles")
        files: list[tuple[zipfile.ZipInfo, tuple[str, ...]]] = []
        seen: set[str] = set()
        for info in entries:
            _check_type(info)
            parts = _safe_parts(info.filename)
            if info.is_dir() or not parts:
                continue
            if len(parts) > limits.max_depth or len("/".join(parts)) > limits.max_path_length:
                raise _reject("경로가 너무 깊거나 깁니다.", "UnsafePath", info.filename)
            # Names differing only by case or Unicode form would overwrite each other on disk.
            key = unicodedata.normalize("NFC", "/".join(parts)).casefold()
            if key in seen:
                raise _reject("같은 이름의 파일이 중복됩니다.", "DuplicateName", info.filename)
            seen.add(key)
            files.append((info, parts))
        if not files:
            raise _reject("zip이 비어 있습니다.", "EmptyArchive")
        if len(files) > limits.max_files:
            raise _reject(f"파일은 {limits.max_files}개까지 올릴 수 있습니다.", "TooManyFiles")
        # A GitHub-style archive wraps everything in one folder: unwrap it.
        roots = {parts[0] for _, parts in files}
        if len(roots) == 1 and all(len(parts) > 1 for _, parts in files):
            files = [(info, parts[1:]) for info, parts in files]

        report, sensitive, selected = SourceReport(), [], []
        for info, parts in files:
            relative = PurePosixPath(*parts)
            decision, reason = classify(relative)
            if decision == Decision.SENSITIVE:
                sensitive.append({"path": str(relative), "reason": reason})
            elif decision == Decision.EXCLUDE:
                report.exclude(str(relative), reason)
            else:
                selected.append((info, relative))
        reject_sensitive(sensitive)
        report.included = sorted(str(relative) for _, relative in selected)
        require_terraform(report)

        too_large = UploadTooLargeError("압축을 푼 크기가 제한을 넘습니다.", "UncompressedTooLarge")
        bomb = _reject("압축률이 비정상적으로 높아 올릴 수 없습니다.", "CompressionBomb")
        # Declared sizes are only an early exit; the real limit is enforced while writing.
        if sum(info.file_size for info, _ in selected) > limits.max_uncompressed_bytes:
            raise too_large
        destination.mkdir(parents=True, mode=0o700)
        root = destination.resolve()
        total = compressed_total = 0
        for info, relative in selected:
            target = (root / relative).resolve()
            if not target.is_relative_to(root):
                raise _reject("허용되지 않는 경로입니다.", "UnsafePath", info.filename)
            target.parent.mkdir(parents=True, exist_ok=True)
            written = 0
            try:
                with archive.open(info) as source, open(target, "xb") as output:
                    while chunk := source.read(CHUNK):
                        written += len(chunk)
                        total += len(chunk)
                        if total > limits.max_uncompressed_bytes:
                            raise too_large
                        if (
                            written > limits.ratio_floor_bytes
                            and written > max(info.compress_size, 1) * limits.max_ratio
                        ):
                            raise bomb
                        output.write(chunk)
            except (
                zipfile.BadZipFile,
                zlib.error,
                EOFError,
                NotImplementedError,
                RuntimeError,
                OSError,
            ):
                # Size or CRC lies, unsupported methods and truncated data all end up here.
                raise _reject(
                    "zip 내용이 손상되었거나 헤더와 맞지 않습니다.", "InvalidArchive", str(relative)
                ) from None
            target.chmod(0o644)
            compressed_total += info.compress_size
        if total > limits.ratio_floor_bytes and total > max(compressed_total, 1) * limits.max_ratio:
            raise bomb
        report.size_bytes = total
        return report
