"""Парсер ВРП города Москвы (`ВРП с 1998 года.xlsx`).

Листы 1 (1998-2015) и 2 (2016-2024+) — на каждом 5 строк-показателей,
различаемых по подстроке в первой колонке:
  - ВРП в текущих ценах, млн руб        → metric=vrp_total
  - Индекс физ.объёма ВРП, %             → metric=vrp_index
  - ВРП на душу населения, руб           → metric=vrp_per_capita
  - Индекс физ.объёма ВРП на душу, %     → metric=vrp_per_capita_index
  - Доля Москвы в суммарном ВРП РФ, %     → metric=vrp_share_rf
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import (
    as_float,
    find_year_row,
    to_frame,
    _record,
)

REGION = "Москва"
INDICATOR_ID = "vrp_msk"
INDICATOR_TITLE = "Валовой региональный продукт г. Москвы"

# (metric, unit). Порядок проверки важен: «на душу» проверяется до общих.
_METRIC_UNITS = {
    "vrp_total": "млн руб",
    "vrp_index": "%",
    "vrp_per_capita": "руб",
    "vrp_per_capita_index": "%",
    "vrp_share_rf": "%",
}


def _classify(label: str) -> str | None:
    s = label.lower()
    has_dushu = "на душу" in s
    has_index = "индекс" in s
    if "доля" in s:
        return "vrp_share_rf"
    if has_dushu and has_index:
        return "vrp_per_capita_index"
    if has_dushu:
        return "vrp_per_capita"
    if has_index:
        return "vrp_index"
    if "валов" in s and "регион" in s:
        return "vrp_total"
    return None


def _parse_sheet(df: pd.DataFrame, source_file: str) -> list[dict]:
    yr = find_year_row(df)
    if yr is None:
        return []
    yrow, year_cols = yr
    out: list[dict] = []
    for r in range(yrow + 1, len(df)):
        label = df.iloc[r, 0]
        if pd.isna(label):
            continue
        metric = _classify(str(label))
        if metric is None:
            continue
        for c, year in year_cols.items():
            v = as_float(df.iloc[r, c])
            if v is None:
                continue
            out.append(_record(
                indicator_id=INDICATOR_ID, indicator_title=INDICATOR_TITLE,
                view=metric, region=REGION, year=year, metric=metric,
                value=v, unit=_METRIC_UNITS[metric], source_file=source_file,
            ))
    return out


def parse(path: Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path)
    records: list[dict] = []
    for sheet in ("1", "2"):
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        records.extend(_parse_sheet(df, path.name))
    return to_frame(records)
