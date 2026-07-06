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
import uuid
import warnings
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


DEDUP_BASE_COLUMNS = [
    "indicator_id",
    "view",
    "region",
    "year",
    "month",
    "quarter",
    "period_type",
    "unit",
]

DEDUP_IDENTITY_COLUMNS = [
    "metric",
    "metric_column",
    "source_file",
    "source_sheet",
    "registry",
    "region_key",
    "sorting",
    "snapshot_date",
    "report_date",
    "report_period",
    "delivery_year",
    "delivery_year_label",
    "entity_type",
    "entity_name",
    "developer_name",
    "developer_group",
    "developer_inn",
    "object_id",
    "uin",
    "project_name",
    "slug",
    "place",
    "url",
    "address",
    "district",
    "okrug",
    "object_name",
    "commercial_name",
    "object_type",
    "object_subtype",
    "grouping",
    "funding_source",
    "planned_delivery_date",
    "credit_bank",
    "uses_escrow",
    "sales_open",
    "regions_count",
    "regions_as_of",
    "name_table",
]

_ETL_LOCK_HELD = False
_ETL_LOCK_TOKEN: str | None = None


def _try_create_lock_file() -> bool:
    global _ETL_LOCK_HELD, _ETL_LOCK_TOKEN
    try:
        fd = os.open(str(ETL_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    token = uuid.uuid4().hex
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(f"token={token}\n")
        f.write(f"pid={os.getpid()}\n")
        f.write(f"started_at={datetime.now().isoformat(timespec='seconds')}\n")
    _ETL_LOCK_HELD = True
    _ETL_LOCK_TOKEN = token
    return True


def _acquire_lock() -> bool:
    stale_lock = False
    if ETL_LOCK.exists():
        # Стейл-лок: если файл старше 6 часов — считаем зависшим и берём.
        age = time.time() - ETL_LOCK.stat().st_mtime
        if age < 6 * 3600:
            return False
        stale_lock = True
    if stale_lock:
        try:
            ETL_LOCK.unlink()
        except OSError:
            return False
    ETL_LOCK.parent.mkdir(parents=True, exist_ok=True)
    return _try_create_lock_file()


def _release_lock() -> None:
    global _ETL_LOCK_HELD, _ETL_LOCK_TOKEN
    if not _ETL_LOCK_HELD:
        return
    try:
        text = ETL_LOCK.read_text(encoding="utf-8")
        if _ETL_LOCK_TOKEN and f"token={_ETL_LOCK_TOKEN}\n" in text:
            ETL_LOCK.unlink(missing_ok=True)
    except FileNotFoundError:
        pass
    except OSError:
        pass
    _ETL_LOCK_HELD = False
    _ETL_LOCK_TOKEN = None


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


def _write_pickle_atomic(value, target: Path) -> None:
    tmp = target.with_name(f"{target.name}.tmp")
    try:
        pd.to_pickle(value, tmp)
        pd.read_pickle(tmp)
        tmp.replace(target)
    finally:
        try:
            if tmp.exists():
                tmp.unlink()
        except OSError:
            pass


def _deduplicate_processed(df: pd.DataFrame) -> pd.DataFrame:
    has_identity_columns = any(c in df.columns for c in DEDUP_IDENTITY_COLUMNS)
    if has_identity_columns:
        dedup_keys = [c for c in df.columns if c not in {"loaded_at", "value"}]
    else:
        dedup_keys = [
            c for c in [*DEDUP_BASE_COLUMNS, "metric"]
            if c in df.columns
        ]
    if not dedup_keys:
        return df.reset_index(drop=True)
    return df.drop_duplicates(subset=dedup_keys, keep="last").reset_index(drop=True)


def _parse_source_file(parser, path: Path) -> pd.DataFrame:
    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message="Workbook contains no default style.*",
            category=UserWarning,
            module="openpyxl.styles.stylesheet",
        )
        return parser.parse(path)


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
        frames = [_parse_source_file(parser, p) for p in local_files]
        df = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()
        if df.empty:
            audit.skip(indicator.id, reason="парсер вернул пустой DataFrame")
            return
        df = _deduplicate_processed(df)
        _write_pickle_atomic(df, target)
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
