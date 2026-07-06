"""Shared local file discovery for downloader wrappers."""
from __future__ import annotations

from pathlib import Path


TEMP_DOWNLOAD_SUFFIXES = {".crdownload", ".download", ".part", ".tmp"}


def list_local_files(root: Path, patterns: list[str]) -> list[Path]:
    """Return unique active files under root matching any pattern."""
    seen: set[Path] = set()
    out: list[Path] = []
    for pattern in patterns:
        for path in sorted(root.glob(pattern)):
            if not path.is_file():
                continue
            if "_archive" in path.parts:
                continue
            if path.suffix.lower() in TEMP_DOWNLOAD_SUFFIXES:
                continue
            resolved = path.resolve()
            if resolved in seen:
                continue
            seen.add(resolved)
            out.append(path)
    return out
