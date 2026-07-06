"""Обёртка над `erzrf_checker.py` под контракт оркестратора.

Контракт (см. `pipeline/downloaders/__init__.py`):
- `fetch(indicator) -> dict` со скачиванием через сеть;
- `find_files(indicator) -> list[Path]` для offline-режима `--skip-download`.

Внутри переадресует пути erzrf_checker в `paths.DATA_RAW/realty/erzrf/` и
`paths.STATE_DIR/erzrf_state.json`. Source_ids:
- `top_rf` / `top_msk` — соответствующие индикаторы запустят `fetch_top`
  (он сам обходит оба региона, повторный запуск дешёвый: если файлы за
  сегодня уже есть — кэш Chrome их скачает снова, поэтому идемпотентность
  на уровне «уже сегодня бегали» вынесена во второй индикатор);
- `cards` — отдельный индикатор для карточек.
"""
from __future__ import annotations

import sys
from pathlib import Path

from pipeline.paths import DATA_RAW, ROOT, STATE_DIR
from pipeline.registry import Indicator
from pipeline.downloaders.local_files import list_local_files


def _find_local(patterns: list[str]) -> list[Path]:
    return list_local_files(DATA_RAW, patterns)


# Чтобы fetch_top не запускался дважды (для top_rf и top_msk),
# трекаем уже отработанные источники в рамках одного процесса.
_RAN_KEYS: set[str] = set()


def _run_erzrf(ec, key: str) -> list[Path]:
    files, ok = ec.run(only=[key])
    if not ok:
        raise RuntimeError(f"erzrf source failed: {key}")
    return list(files)


def fetch(indicator: Indicator, *, download: bool = True) -> dict:
    if not download:
        return {
            "new_files": [],
            "prev_date": "",
            "new_date": "",
            "skipped": False,
        }

    sys.path.insert(0, str(ROOT))
    import erzrf_checker as ec  # type: ignore

    ec.DOWNLOAD_DIR = DATA_RAW / "realty" / "erzrf"
    ec.CARDS_DIR = ec.DOWNLOAD_DIR / "cards"
    ec.STATE_FILE = STATE_DIR / "erzrf_state.json"

    new_files: list[Path] = []
    wanted = set(indicator.source_ids)

    # top_rf и top_msk оба запускают fetch_top (он обходит обе REGIONS внутри).
    if (wanted & {"top_rf", "top_msk"}) and "top" not in _RAN_KEYS:
        new_files.extend(_run_erzrf(ec, "top"))
        _RAN_KEYS.add("top")

    if "cards" in wanted and "cards" not in _RAN_KEYS:
        new_files.extend(_run_erzrf(ec, "cards"))
        _RAN_KEYS.add("cards")

    state = ec.load_state()
    new_date = ""
    if "erzrf_top" in state and isinstance(state["erzrf_top"], dict):
        new_date = state["erzrf_top"].get("last_run", "")[:10] or new_date

    return {
        "new_files": new_files,
        "prev_date": "",
        "new_date": new_date,
        "skipped": not new_files,
    }


def find_files(indicator: Indicator) -> list[Path]:
    return _find_local(list(indicator.file_patterns))
