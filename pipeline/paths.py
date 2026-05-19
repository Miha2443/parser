"""Кроссплатформенные пути проекта."""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(os.environ.get("PARSER_ROOT", Path(__file__).resolve().parent.parent))

DOWNLOADS_DIR = Path(os.environ.get("PARSER_DOWNLOADS", ROOT / "downloads"))
DATA_DIR = ROOT / "data"
DATA_RAW = DATA_DIR / "raw"
DATA_PROCESSED = DATA_DIR / "processed"


def ensure_dirs() -> None:
    for p in (DATA_RAW, DATA_PROCESSED, DOWNLOADS_DIR):
        p.mkdir(parents=True, exist_ok=True)
