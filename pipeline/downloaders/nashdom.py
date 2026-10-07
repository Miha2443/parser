"""Обёртка над `nashdom_checker.py` под контракт оркестратора.

Контракт (см. `pipeline/downloaders/__init__.py`):
- `fetch(indicator) -> dict` со скачиванием через сеть;
- `find_files(indicator) -> list[Path]` для offline-режима `--skip-download`.

Внутри переадресует пути nashdom_checker в `paths.DATA_RAW/realty/nashdom/`
и `paths.STATE_DIR/nashdom_state.json`, и запускает только ключи из
`indicator.source_ids` (monitoring_2_0 / rasprodannost / kvartirografia /
construction_operational).
"""
from __future__ import annotations

import sys
from pathlib import Path

from pipeline.paths import DATA_RAW, ROOT, STATE_DIR
from pipeline.registry import Indicator
from pipeline.downloaders.local_files import list_local_files


def _find_local(patterns: list[str]) -> list[Path]:
    return list_local_files(DATA_RAW, patterns)


def fetch(indicator: Indicator, *, download: bool = True) -> dict:
    if not download:
        return {
            "new_files": [],
            "prev_date": "",
            "new_date": "",
            "skipped": False,
        }

    sys.path.insert(0, str(ROOT))
    import nashdom_checker as nc  # type: ignore

    nc.DOWNLOAD_DIR = DATA_RAW / "realty" / "nashdom"
    nc.STATE_FILE = STATE_DIR / "nashdom_state.json"

    wanted = set(indicator.source_ids)
    new_files, ok = nc.run(only=wanted)
    if not ok:
        raise RuntimeError(
            "nashdom source failed: " + ", ".join(sorted(wanted))
        )

    state = nc.load_state()
    prev_date = ""
    new_date = ""
    for key in wanted:
        entry = state.get(key, {})
        if isinstance(entry, dict):
            new_date = entry.get("report_date") or new_date

    return {
        "new_files": new_files,
        "prev_date": prev_date,
        "new_date": new_date,
        "skipped": not new_files,
    }


def find_files(indicator: Indicator) -> list[Path]:
    return _find_local(list(indicator.file_patterns))
