"""Тривиальная обёртка для файлов, которые пользователь кладёт руками.

Используется индикатором `realty_escrow_manual` (наполненность счетов эскроу).
Скачивателя нет — `fetch` всегда возвращает `skipped=True`; `find_files`
ищет в `data/raw/<паттерн>`.
"""
from __future__ import annotations

from pathlib import Path

from pipeline.paths import DATA_RAW
from pipeline.registry import Indicator
from pipeline.downloaders.local_files import list_local_files


def _find_local(patterns: list[str]) -> list[Path]:
    return list_local_files(DATA_RAW, patterns)


def fetch(indicator: Indicator, *, download: bool = True) -> dict:
    return {
        "new_files": [],
        "prev_date": "",
        "new_date": "",
        "skipped": True,
    }


def find_files(indicator: Indicator) -> list[Path]:
    return _find_local(list(indicator.file_patterns))
