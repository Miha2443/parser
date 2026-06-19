"""Тривиальная обёртка для файлов, которые пользователь кладёт руками.

Используется индикатором `realty_escrow_manual` (наполненность счетов эскроу).
Скачивателя нет — `fetch` всегда возвращает `skipped=True`; `find_files`
ищет в `data/raw/<паттерн>`.
"""
from __future__ import annotations

from pathlib import Path

from pipeline.paths import DATA_RAW
from pipeline.registry import Indicator


def _find_local(patterns: list[str]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pat in patterns:
        for p in sorted(DATA_RAW.glob(pat)):
            r = p.resolve()
            if r in seen:
                continue
            seen.add(r)
            out.append(p)
    return out


def fetch(indicator: Indicator, *, download: bool = True) -> dict:
    return {
        "new_files": [],
        "prev_date": "",
        "new_date": "",
        "skipped": True,
    }


def find_files(indicator: Indicator) -> list[Path]:
    return _find_local(list(indicator.file_patterns))
