"""Парсер xls fedstat 57824 — Среднемесячная номинальная начисленная заработная плата.

Структура листа `Данные`:
- строка 2 — регионы (forward-fill, по 5 колонок на регион: 2017..2021/2022..2026)
- строка 3 — года
- колонка 0 — отрасль (с отступами по уровню иерархии ОКВЭД)
- колонка 1 — период (`январь`, `февраль`, `январь-февраль`, ...)
- значения с (row=4, col=2)

Мы оставляем только два разреза: «Всего» и «Строительство», и два региона: РФ и Москва.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .common import (
    DATA_COLUMNS,
    MONTHS,
    MONTH_NAME_BY_NUM,
    QUARTER_BY_MONTH,
    REGION_CLEAN,
    REGIONS_KEEP,
)

SECTION = "employment_salary"
INDICATOR_ID = "avg_salary"
INDICATOR_TITLE = "Среднемесячная номинальная начисленная заработная плата"
UNIT = "руб"

VIEW_MAP = {
    "Всего по обследуемым видам экономической деятельности": "Всего",
    "    СТРОИТЕЛЬСТВО": "Строительство",
}


def parse(xls_path: Path) -> pd.DataFrame:
    df = pd.read_excel(xls_path, sheet_name="Данные", header=None)
    regions = df.iloc[2].ffill()
    years = df.iloc[3]
    loaded_at = datetime.now()
    source_file = xls_path.name

    records: list[dict] = []

    for row_idx in range(4, len(df)):
        view_raw = df.iat[row_idx, 0]
        period_raw = df.iat[row_idx, 1]
        if pd.isna(view_raw) or pd.isna(period_raw):
            continue
        view = VIEW_MAP.get(str(view_raw))
        if view is None:
            continue

        period_str = str(period_raw).strip().lower()
        if "-" in period_str:
            last_month_name = period_str.split("-")[-1].strip()
            period_type = "ytd"
        else:
            last_month_name = period_str
            period_type = "month"
        month_num = MONTHS.get(last_month_name)
        if month_num is None:
            continue

        for col in range(2, df.shape[1]):
            value = df.iat[row_idx, col]
            if pd.isna(value):
                continue
            region_raw = regions.iat[col]
            year_raw = years.iat[col]
            if pd.isna(region_raw) or pd.isna(year_raw):
                continue
            region = REGION_CLEAN.get(str(region_raw).strip(), str(region_raw).strip())
            if region not in REGIONS_KEEP:
                continue
            try:
                value_num = float(value)
                year = int(float(year_raw))
            except (TypeError, ValueError):
                continue

            records.append({
                "section": SECTION,
                "indicator_id": INDICATOR_ID,
                "indicator_title": INDICATOR_TITLE,
                "view": view,
                "region": region,
                "year": year,
                "month": month_num,
                "quarter": QUARTER_BY_MONTH[month_num],
                "period_type": period_type,
                "value": round(value_num, 4),
                "unit": UNIT,
                "source_file": source_file,
                "loaded_at": loaded_at,
            })

    out = pd.DataFrame.from_records(records, columns=DATA_COLUMNS)
    out = out.drop_duplicates(
        subset=["indicator_id", "view", "region", "year", "month", "period_type"],
        keep="last",
    ).reset_index(drop=True)
    return out


__all__ = ["parse", "SECTION", "INDICATOR_ID", "INDICATOR_TITLE"]
