"""Обёртка над `rosstat_checker.py` под контракт оркестратора.

Контракт (см. `pipeline/downloaders/__init__.py`):
- `fetch(indicator) -> dict` со скачиванием через сеть;
- `find_files(indicator) -> list[Path]` для offline-режима `--skip-download`.

Внутри переадресует пути rosstat_checker в `paths.DOWNLOADS_DIR` и
`paths.STATE_DIR / 'rosstat_state.json'`, обходит только нужный
indicator.source_ids (ключи из PAGE_SOURCES) и архивирует старые xlsx
по тем же паттернам, что и fedstat-downloader.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

import requests

from pipeline.paths import DATA_ARCHIVE, DOWNLOADS_DIR, ROOT, STATE_DIR
from pipeline.registry import Indicator


def _find_local(patterns: list[str]) -> list[Path]:
    seen: set[Path] = set()
    out: list[Path] = []
    for pat in patterns:
        for p in sorted(DOWNLOADS_DIR.glob(pat)):
            r = p.resolve()
            if r in seen:
                continue
            seen.add(r)
            out.append(p)
    return out


def _archive_old(patterns: list[str], keep: list[Path]) -> None:
    keep_set = {p.resolve() for p in keep}
    today = datetime.now().strftime("%Y-%m-%d")
    archive_dir = DATA_ARCHIVE / today
    for pat in patterns:
        for f in DOWNLOADS_DIR.glob(pat):
            if f.resolve() in keep_set:
                continue
            archive_dir.mkdir(parents=True, exist_ok=True)
            target = archive_dir / f.name
            if target.exists():
                target = archive_dir / f"{f.stem}_{datetime.now().strftime('%H%M%S')}{f.suffix}"
            shutil.move(str(f), str(target))


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
    rc.STATE_FILE = STATE_DIR / "rosstat_state.json"

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

            if remote_date and saved_date == remote_date:
                continue

            filename = rc._filename_for(match["href"])
            save_path = DOWNLOADS_DIR / filename
            if rc.download_file(session, match["href"], referer=url, save_path=save_path):
                new_files.append(save_path)
                state[key] = {
                    "date": remote_date or saved_date or "",
                    "filename": filename,
                    "url": match["href"],
                }

    rc.save_state(state)

    if new_files:
        _archive_old(list(indicator.file_patterns), keep=new_files)

    return {
        "new_files": new_files,
        "prev_date": prev_date,
        "new_date": new_date,
        "skipped": not new_files,
    }


def find_files(indicator: Indicator) -> list[Path]:
    return _find_local(list(indicator.file_patterns))
