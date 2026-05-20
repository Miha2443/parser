"""ETL: читает xls из downloads/, парсит и пишет Parquet в data/processed/.

Запуск:
    python pipeline/run_etl.py
"""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd

# Позволяем запуск как `python pipeline/run_etl.py`
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from pipeline.parsers import fedstat_ipc, fedstat_salary
from pipeline.paths import DATA_PROCESSED, DOWNLOADS_DIR, ensure_dirs

SALARY_PATTERN = "*Среднемесячная номинальная начисленная заработная плата*.xls*"
IPC_PART1_PATTERN = "*Индексы потребительских цен*часть1*.xls*"
IPC_PART2_PATTERN = "*Индексы потребительских цен*часть2*.xls*"


def find_latest(pattern: str) -> Path | None:
    files = sorted(DOWNLOADS_DIR.glob(pattern))
    return files[-1] if files else None


def write_table(df: pd.DataFrame, name: str) -> Path:
    """Сохраняет DataFrame в pickle (stdlib, без внешних зависимостей)."""
    path = DATA_PROCESSED / f"{name}.pkl"
    df.to_pickle(path)
    return path


def run() -> None:
    ensure_dirs()
    print(f"📂 Downloads: {DOWNLOADS_DIR}")
    print(f"📂 Processed: {DATA_PROCESSED}\n")

    # Зарплата
    salary_path = find_latest(SALARY_PATTERN)
    if salary_path:
        print(f"⚙️  ЗП: {salary_path.name}")
        salary_df = fedstat_salary.parse(salary_path)
        out = write_table(salary_df, "employment_salary")
        print(f"   ✓ {len(salary_df)} строк, годы {salary_df['year'].min()}–{salary_df['year'].max()}")
        print(f"   ✓ Записан {out}\n")
    else:
        print(f"⚠️  Файл ЗП не найден по {SALARY_PATTERN}\n")

    # ИПЦ — две части, объединяем
    ipc_p1 = find_latest(IPC_PART1_PATTERN)
    ipc_p2 = find_latest(IPC_PART2_PATTERN)
    ipc_frames: list[pd.DataFrame] = []
    if ipc_p1:
        print(f"⚙️  ИПЦ ч.1: {ipc_p1.name}")
        ipc_frames.append(fedstat_ipc.parse(ipc_p1))
    if ipc_p2:
        print(f"⚙️  ИПЦ ч.2: {ipc_p2.name}")
        ipc_frames.append(fedstat_ipc.parse(ipc_p2))
    if ipc_frames:
        ipc_df = pd.concat(ipc_frames, ignore_index=True)
        ipc_df = ipc_df.drop_duplicates(
            subset=["indicator_id", "view", "region", "year", "month", "period_type"],
            keep="last",
        ).reset_index(drop=True)
        out = write_table(ipc_df, "prices_ipc")
        print(f"   ✓ {len(ipc_df)} строк, годы {ipc_df['year'].min()}–{ipc_df['year'].max()}")
        print(f"   ✓ Записан {out}\n")
    else:
        print("⚠️  Файлы ИПЦ не найдены\n")

    print("Готово ✓")


if __name__ == "__main__":
    run()
