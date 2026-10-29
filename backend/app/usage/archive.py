"""Builds the download: the stored snapshot under modules/<label>/ plus generated root files."""

import io
import tarfile
import tempfile
import zipfile
from pathlib import Path, PurePosixPath
from typing import IO

EPOCH = (1980, 1, 1, 0, 0, 0)
CHUNK = 256 * 1024


def _safe_member(name: str) -> bool:
    path = PurePosixPath(name)
    return (
        bool(path.parts)
        and not path.is_absolute()
        and all(part not in ("", ".", "..") for part in path.parts)
        and "\\" not in name
        and ":" not in path.parts[0]
    )


def _entry(name: str, mode: int = 0o644) -> zipfile.ZipInfo:
    info = zipfile.ZipInfo(name, EPOCH)
    info.compress_type = zipfile.ZIP_DEFLATED
    info.external_attr = (0o100000 | mode) << 16
    return info


def build_download(snapshot: Path | bytes, label: str, root_files: dict[str, str]) -> IO[bytes]:
    """A zip in an anonymous temporary file, positioned at the start.

    Only regular files are copied out of the snapshot; links and odd paths are skipped even
    though we wrote the snapshot ourselves.
    """
    output = tempfile.TemporaryFile()
    try:
        with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
            for name, text in root_files.items():
                archive.writestr(_entry(name), text.encode("utf-8"))
            with (
                tarfile.open(fileobj=io.BytesIO(snapshot), mode="r:gz")
                if isinstance(snapshot, bytes)
                else tarfile.open(snapshot, mode="r:gz")
            ) as source:
                for member in source:
                    if not member.isfile() or not _safe_member(member.name):
                        continue
                    content = source.extractfile(member)
                    if content is None:
                        continue
                    mode = 0o755 if member.mode & 0o100 else 0o644
                    target = _entry(f"modules/{label}/{member.name}", mode)
                    with archive.open(target, "w") as written:
                        while chunk := content.read(CHUNK):
                            written.write(chunk)
        output.seek(0)
        return output
    except BaseException:
        output.close()
        raise
