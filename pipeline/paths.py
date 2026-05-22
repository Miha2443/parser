"""Кроссплатформенные пути проекта.

Все пути собираются из переменных окружения (если заданы) или относительно корня
репозитория. Никаких raw-строк `C:\\...` в коде.
"""
from __future__ import annotations
import os
from pathlib import Path

ROOT = Path(os.environ.get("PARSER_ROOT", Path(__file__).resolve().parent.parent))

DOWNLOADS_DIR = Path(os.environ.get("PARSER_DOWNLOADS", ROOT / "downloads"))
DATA_DIR = ROOT / "data"
DATA_RAW = DATA_DIR / "raw"
DATA_PROCESSED = DATA_DIR / "processed"
DATA_ARCHIVE = DATA_RAW / "_archive"

STATE_DIR = Path(os.environ.get("PARSER_STATE", ROOT / "state"))
CONFIG_DIR = ROOT / "config"

ETL_AUDIT_LOG = DATA_PROCESSED / "etl_audit.jsonl"
ETL_LOCK = STATE_DIR / ".etl.lock"


def ensure_dirs() -> None:
    for p in (DATA_RAW, DATA_PROCESSED, DATA_ARCHIVE, DOWNLOADS_DIR, STATE_DIR, CONFIG_DIR):
        p.mkdir(parents=True, exist_ok=True)
