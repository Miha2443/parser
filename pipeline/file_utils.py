"""Small file I/O helpers shared by downloaders."""
from __future__ import annotations

from pathlib import Path
from typing import Any, Callable


def _tmp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def write_bytes_atomic(
    path: Path,
    content: bytes,
    *,
    allow_empty: bool = False,
    validate: Callable[[Path], None] | None = None,
) -> int:
    """Write bytes through a temporary file and atomic replace."""
    if not content and not allow_empty:
        raise ValueError(f"refusing to write empty file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_bytes(content)
        if validate is not None:
            validate(tmp)
        tmp.replace(path)
        return len(content)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def stream_response_atomic(
    response: Any,
    path: Path,
    *,
    chunk_size: int = 8192,
    allow_empty: bool = False,
    validate: Callable[[Path], None] | None = None,
) -> int:
    """Stream a requests-like response to path without exposing partial files."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    written = 0
    try:
        with open(tmp, "wb") as f:
            for chunk in response.iter_content(chunk_size=chunk_size):
                if not chunk:
                    continue
                f.write(chunk)
                written += len(chunk)
        if written == 0 and not allow_empty:
            raise ValueError(f"refusing to write empty file: {path}")
        if validate is not None:
            validate(tmp)
        tmp.replace(path)
        return written
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def validate_excel_file(path: Path) -> None:
    """Accept binary XLS/OLE2 and XLSX/ZIP files, reject common HTML/text responses."""
    with open(path, "rb") as f:
        head = f.read(8)
    if head.startswith(b"PK\x03\x04") or head.startswith(b"PK\x05\x06") or head.startswith(b"PK\x07\x08"):
        return
    if head.startswith(b"\xD0\xCF\x11\xE0\xA1\xB1\x1A\xE1"):
        return
    stripped = head.lstrip()
    if stripped.startswith(b"<"):
        raise ValueError(f"downloaded file looks like HTML, not Excel: {path}")
    raise ValueError(f"downloaded file is not a recognized Excel binary: {path}")
