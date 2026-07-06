"""Helpers for choosing realty files to send to TDM."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path


TDM_FILE_SUFFIXES = {".xlsx", ".json", ".csv", ".pdf", ".png"}
TEMP_DOWNLOAD_SUFFIXES = {".crdownload", ".download", ".part", ".tmp"}


@dataclass(frozen=True)
class TdmFileOption:
    path: Path
    rel: str
    mtime: float
    size_bytes: int

    @property
    def label(self) -> str:
        mtime_text = datetime.fromtimestamp(self.mtime).strftime("%d.%m.%Y %H:%M")
        size_kb = self.size_bytes / 1024
        size_text = f"{size_kb:.0f} KB" if size_kb < 1024 else f"{size_kb / 1024:.1f} MB"
        return f"{self.rel}  ({mtime_text}, {size_text})"


def list_tdm_realty_files(realty_root: Path) -> list[TdmFileOption]:
    """Return sendable active realty files sorted newest first."""
    if not realty_root.exists():
        return []

    options: list[TdmFileOption] = []
    for path in realty_root.rglob("*"):
        if not path.is_file() or "_archive" in path.parts:
            continue
        suffix = path.suffix.lower()
        if suffix in TEMP_DOWNLOAD_SUFFIXES or suffix not in TDM_FILE_SUFFIXES:
            continue
        try:
            stat = path.stat()
            rel = str(path.relative_to(realty_root))
        except OSError:
            continue
        options.append(
            TdmFileOption(
                path=path,
                rel=rel,
                mtime=stat.st_mtime,
                size_bytes=stat.st_size,
            )
        )
    options.sort(key=lambda item: item.mtime, reverse=True)
    return options
