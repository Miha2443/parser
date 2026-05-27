"""Best-effort оркестратор полного цикла ETL.

Один проход:
1. Для каждого индикатора из реестра:
   - скачивает (если есть интернет и Chrome);
   - парсит свежий xls;
   - перезаписывает витрину `data/processed/<id>.pkl`;
   - в audit log пишет статус (success / skip / error).
2. Падение одного индикатора не валит остальные.
3. По завершении — сводка в audit log + (опционально) Telegram.

Запуск:
    py pipeline/orchestrator.py                  # полный цикл с скачиванием
    py pipeline/orchestrator.py --skip-download  # без сети, по xls в downloads/

Защита от наложений: lock-файл `state/.etl.lock`. Если предыдущий ETL ещё работает —
завершаемся без действий.
"""
from __future__ import annotations

import argparse
import importlib
import os
import sys
import time
import traceback
from datetime import datetime
from pathlib import Path

# Запуск как `python pipeline/orchestrator.py` из корня.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pandas as pd

from pipeline.audit import AuditRun
from pipeline.notifier import send_critical, send_summary
from pipeline.paths import (
    DATA_PROCESSED,
    DOWNLOADS_DIR,
    ETL_LOCK,
    ensure_dirs,
)
from pipeline.registry import INDICATORS, Indicator


def _acquire_lock() -> bool:
    if ETL_LOCK.exists():
        # Стейл-лок: если файл старше 6 часов — считаем зависшим и берём.
        age = time.time() - ETL_LOCK.stat().st_mtime
        if age < 6 * 3600:
            return False
    ETL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    ETL_LOCK.write_text(f"{os.getpid()} {datetime.now().isoformat()}\n", encoding="utf-8")
    return True


def _release_lock() -> None:
    try:
        ETL_LOCK.unlink(missing_ok=True)
    except OSError:
        pass


def _parser_module(name: str):
    return importlib.import_module(f"pipeline.parsers.{name}")


def _downloader(source: str):
    """Возвращает модуль downloader. Для отсутствующих источников вернёт None."""
    try:
        return importlib.import_module(f"pipeline.downloaders.{source}")
    except ImportError:
        return None


def _files_newer(files: list[Path], target: Path) -> bool:
    """True, если витрины ещё нет или хотя бы один исходник свежее неё (по mtime)."""
    if not target.exists():
        return True
    t = target.stat().st_mtime
    return any(p.exists() and p.stat().st_mtime > t for p in files)


def _process_one(
    indicator: Indicator, audit: AuditRun, *, download: bool
) -> None:
    started = time.time()
    dl = _downloader(indicator.source)
    if dl is None:
        audit.error(
            indicator.id,
            RuntimeError(f"downloader для источника '{indicator.source}' не реализован"),
        )
        return

    new_files: list[Path] = []
    prev_date = ""
    new_date = ""
    if download and hasattr(dl, "fetch"):
        try:
            result = dl.fetch(indicator)
        except Exception as exc:
            audit.error(indicator.id, exc, stage="download")
            return
        new_files = list(result.get("new_files") or [])
        prev_date = result.get("prev_date", "")
        new_date = result.get("new_date", "")

    # Парсим все локальные файлы по паттернам (часть1+часть2 и т.п.), а не только свежескачанные.
    local_files = list(dl.find_files(indicator))
    if not local_files:
        audit.skip(indicator.id, reason="нет xls в downloads/", prev_date=prev_date, new_date=new_date)
        return

    target = DATA_PROCESSED / f"{indicator.id}.pkl"
    # В режиме скачивания не пересобираем витрину зря: только если что-то скачали,
    # витрины ещё нет или локальный файл свежее неё. `--skip-download` собирает всегда.
    if download and not new_files and not _files_newer(local_files, target):
        audit.skip(indicator.id, reason="не изменилось на источнике", prev_date=prev_date, new_date=new_date)
        return

    try:
        parser = _parser_module(indicator.parser)
        frames = [parser.parse(p) for p in local_files]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            audit.skip(indicator.id, reason="парсер вернул пустой DataFrame")
            return
        dedup_keys = [
            c for c in ["indicator_id", "view", "region", "year", "month", "period_type", "metric"]
            if c in df.columns
        ]
        df = df.drop_duplicates(subset=dedup_keys, keep="last").reset_index(drop=True)
        df.to_pickle(target)
        audit.success(
            indicator.id,
            rows=len(df),
            files=[str(p.name) for p in local_files],
            target=str(target.name),
            prev_date=prev_date,
            new_date=new_date,
            duration_sec=round(time.time() - started, 2),
            year_min=int(df["year"].min()),
            year_max=int(df["year"].max()),
        )
    except Exception as exc:
        audit.error(indicator.id, exc, stage="parse", files=[str(p) for p in local_files])


def run_all(*, download: bool = True) -> int:
    ensure_dirs()
    if not _acquire_lock():
        print("⚠️  Предыдущий ETL ещё выполняется — выходим без действий.")
        return 0

    audit = AuditRun()
    try:
        print(f"🚀 ETL run_id={audit.run_id}, download={download}")
        print(f"   downloads={DOWNLOADS_DIR}")
        print(f"   processed={DATA_PROCESSED}\n")
        for ind in INDICATORS:
            print(f"➡️  {ind.id}")
            _process_one(ind, audit, download=download)
        summary = audit.finalize()
        print("\n──")
        print(f"Итог: success={summary['success']}, skip={summary['skip']}, error={summary['error']}")
        send_summary(audit.entries)
        return 1 if summary["error"] else 0
    except Exception:
        send_critical(traceback.format_exc())
        raise
    finally:
        _release_lock()


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--skip-download",
        action="store_true",
        help="не лезть в сеть, использовать xls из downloads/",
    )
    args = ap.parse_args()
    return run_all(download=not args.skip_download)


if __name__ == "__main__":
    sys.exit(main())
