"""Small file I/O helpers shared by downloaders."""
from __future__ import annotations

from pathlib import Path
from typing import Any


def _tmp_path(path: Path) -> Path:
    return path.with_name(f"{path.name}.tmp")


def write_bytes_atomic(path: Path, content: bytes, *, allow_empty: bool = False) -> int:
    """Write bytes through a temporary file and atomic replace."""
    if not content and not allow_empty:
        raise ValueError(f"refusing to write empty file: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = _tmp_path(path)
    try:
        tmp.write_bytes(content)
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
        tmp.replace(path)
        return written
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass
