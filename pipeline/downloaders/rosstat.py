"""Обёртка над `rosstat_checker.py` под контракт оркестратора.

Контракт (см. `pipeline/downloaders/__init__.py`):
- `fetch(indicator) -> dict` со скачиванием через сеть;
- `find_files(indicator) -> list[Path]` для offline-режима `--skip-download`.

Внутри переадресует пути rosstat_checker в `paths.DOWNLOADS_DIR` и
`paths.STATE_DIR / 'rosstat_state.json'`, обходит только нужный
indicator.source_ids (ключи из PAGE_SOURCES) и архивирует только
подтверждённые предыдущие версии этого ключа.
"""
from __future__ import annotations

import sys
from pathlib import Path

import requests

from pipeline.paths import DOWNLOADS_DIR, ROOT
from pipeline.registry import Indicator
from pipeline.downloaders.local_files import list_local_files


def _find_local(patterns: list[str]) -> list[Path]:
    return list_local_files(DOWNLOADS_DIR, patterns)


def _archive_old(patterns: list[str], keep: list[Path]) -> None:
    """Legacy wildcard API deliberately does nothing; retire by source key only."""


def fetch(indicator: Indicator, *, download: bool = True) -> dict:
    if not download:
        return {
            "new_files": _find_local(list(indicator.file_patterns)),
            "prev_date": "",
            "new_date": "",
            "skipped": False,
        }

    sys.path.insert(0, str(ROOT))
    import rosstat_checker as rc  # type: ignore

    rc.DOWNLOAD_DIR = DOWNLOADS_DIR
    # Единое состояние с standalone-чекером (py rosstat_checker.py).
    rc.STATE_FILE = ROOT / "state/rosstat_state.json"

    state = rc.load_state()
    session = rc._new_session()

    # Какие именно ключи (source_ids) этого индикатора нужно проверить.
    wanted_keys = set(indicator.source_ids)

    new_files: list[Path] = []
    prev_date = ""
    new_date = ""

    for url, sources in rc.PAGE_SOURCES.items():
        # На странице может не быть ни одного нужного ключа — тогда не лезем.
        page_keys = [s for s in sources if s["key"] in wanted_keys]
        if not page_keys:
            continue
        try:
            items = rc.get_files_on_page(session, url)
        except requests.RequestException as exc:
            print(f"  ❌ {url}: {exc}")
            continue

        for src in page_keys:
            key = src["key"]
            match = rc._match_source(items, src["match_any"])
            if not match:
                continue
            remote_date = match["date"] or ""
            saved = state.get(key, {})
            saved_date = saved.get("date") if isinstance(saved, dict) else ""
            prev_date = prev_date or (saved_date or "")
            new_date = remote_date or new_date

            saved_name = saved.get("filename", "") if isinstance(saved, dict) else ""
            if (remote_date and saved_date == remote_date and saved_name
                    and Path(saved_name).name == saved_name
                    and rc.confirmed_download(DOWNLOADS_DIR / saved_name, source_id=key, remote_date=remote_date)):
                if isinstance(saved, dict) and Path(saved.get("filename", "")).name == saved.get("filename"):
                    rc.retire_download(key, DOWNLOADS_DIR / saved["filename"], saved_date)
                continue

            filename = rc._filename_for(match["href"], remote_date=remote_date)
            save_path = DOWNLOADS_DIR / filename
            if rc.download_file(session, match["href"], referer=url, save_path=save_path,
                                source_key=key, remote_date=remote_date):
                new_files.append(save_path)
                state[key] = {
                    "date": remote_date or saved_date or "",
                    "filename": filename,
                    "url": match["href"],
                }
                rc.save_state(state)
                rc.retire_download(key, save_path, remote_date, saved.get("filename") if isinstance(saved, dict) else None)

    rc.save_state(state)


    return {
        "new_files": new_files,
        "prev_date": prev_date,
        "new_date": new_date,
        "skipped": not new_files,
    }


def find_files(indicator: Indicator) -> list[Path]:
    return _find_local(list(indicator.file_patterns))
