"""Обёртка над fedstat_checker.py.

Когда вызвана `fetch(indicator, download=True)`:
- импортирует существующий `fedstat_checker`;
- переопределяет `DOWNLOAD_DIR` и `STATE_FILE` через monkey-patch его модульных переменных;
- по очереди обходит `indicator.source_ids`, проверяет дату «Последнее обновление данных»,
  скачивает xls если поменялся;
- старые версии перемещает в `data/raw/_archive/YYYY-MM-DD/`.

`download=False` — пропускаем сеть, ищем файлы локально по `indicator.file_patterns`.
"""
from __future__ import annotations

import shutil
import sys
from datetime import datetime
from pathlib import Path

from pipeline.paths import DATA_ARCHIVE, DOWNLOADS_DIR, ROOT
from pipeline.registry import Indicator
from pipeline.downloaders.local_files import list_local_files


def _find_local(patterns: list[str]) -> list[Path]:
    """Все файлы, попадающие под любой из паттернов, уникальные по resolved-пути.

    Возвращаем все, а не только свежие: в переходный период (старая разделённая
    выгрузка + новая объединённая) парсер должен видеть оба источника. Дубликаты
    схлопываются `drop_duplicates` в оркестраторе.
    """
    return list_local_files(DOWNLOADS_DIR, patterns)


def _archive_old(patterns: list[str], keep: list[Path]) -> None:
    """Перемещает старые файлы по паттернам в архив (кроме `keep`)."""
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
    """Скачивает обновления fedstat-индикаторов (или находит локальные при download=False)."""
    if not download:
        return {
            "new_files": _find_local(list(indicator.file_patterns)),
            "prev_date": "",
            "new_date": "",
            "skipped": False,
        }

    # Импортируем существующий fedstat_checker и подменяем пути.
    sys.path.insert(0, str(ROOT))
    import fedstat_checker as fc  # type: ignore

    fc.DOWNLOAD_DIR = DOWNLOADS_DIR
    # Единое состояние с standalone-чекером (py fedstat_checker.py): один файл,
    # где хранятся даты обновления данных на сайте — чтобы не качать дважды.
    fc.STATE_FILE = ROOT / "fedstat_state.json"

    state = fc.load_state()
    driver = fc.create_driver(download_dir=DOWNLOADS_DIR)
    new_files: list[Path] = []
    prev_date = ""
    new_date = ""
    failures: list[str] = []
    unchanged = False
    try:
        for src_id in indicator.source_ids:
            remote_date = fc.get_last_update_date(driver, src_id)
            if remote_date is None:
                failures.append(f"{src_id}: не удалось получить дату обновления")
                continue
            saved_date = state.get(src_id)
            prev_date = prev_date or (saved_date or "")
            new_date = remote_date
            if saved_date == remote_date:
                unchanged = True
                continue
            saved_path = fc.download_excel(
                src_id, DOWNLOADS_DIR, remote_date=remote_date, driver=driver
            )
            if saved_path:
                new_files.append(Path(saved_path))
                state[src_id] = remote_date
            else:
                failures.append(f"{src_id}: обновился, но не скачался")
    finally:
        driver.quit()
        fc.save_state(state)

    # Оркестратор записывает исключение как stage=download, а не «без изменений».
    # Успешные загрузки остаются в state; старые файлы при неполном запуске сохраняем.
    if failures:
        raise RuntimeError("Fedstat: " + "; ".join(failures))

    # Паттерны общие для нескольких source_ids: при частичном обновлении нельзя
    # архивировать файлы неизменившихся источников вместе со старыми версиями.
    if new_files and not unchanged:
        _archive_old(list(indicator.file_patterns), keep=new_files)

    return {
        "new_files": new_files,
        "prev_date": prev_date,
        "new_date": new_date,
        "skipped": not new_files,
    }


def find_files(indicator: Indicator) -> list[Path]:
    """Локальный поиск без скачивания — используется при `--skip-download`."""
    return _find_local(list(indicator.file_patterns))
