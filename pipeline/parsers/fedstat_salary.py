"""Парсер xls по Среднемесячной номинальной начисленной заработной плате.

Поддерживает два формата выгрузки fedstat:
- `57824` («с 2017 г.»): годы в строке 3 (колонки), регион в строке 2 (с ffill),
  отрасль в колонке 0, период (месяц/«январь-XXX») в колонке 1, значения с (4, 2).
- `43246` («по 2016 г.»): транспонировано — годы в строке 2 (с ffill, по 23 колонки),
  период в строке 3 (12 месяцев + 11 YTD), отрасль в колонке 0, регион в колонке 1,
  значения с (4, 2).

Различение — по заголовку в `df.iat[0, 0]` («с 2017 г.» vs «по 2016 г.»).

Мы оставляем только «Всего» + «Строительство», и регионы РФ + Москва.
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from .common import (
    DATA_COLUMNS,
    MONTHS,
    QUARTER_BY_MONTH,
    clean_region,
)

SECTION = "employment_salary"
INDICATOR_ID = "avg_salary"
INDICATOR_TITLE = "Среднемесячная номинальная начисленная заработная плата"
UNIT = "руб"

# Названия отраслей различаются между двумя выгрузками — карта общая, обе ветки её используют.
VIEW_MAP = {
    # Формат «с 2017 г.» (ОКВЭД 2)
    "Всего по обследуемым видам экономической деятельности": "Всего",
    "    СТРОИТЕЛЬСТВО": "Строительство",
    # Формат «по 2016 г.» (ОКВЭД)
    "Всего": "Всего",
    "РАЗДЕЛ F   СТРОИТЕЛЬСТВО": "Строительство",
}


def parse(xls_path: Path) -> pd.DataFrame:
    df = pd.read_excel(xls_path, sheet_name="Данные", header=None)
    title = str(df.iat[0, 0]) if pd.notna(df.iat[0, 0]) else ""
    if "по 2016" in title:
        records = _parse_pre2017(df)
    else:
        records = _parse_post2017(df)

    loaded_at = datetime.now()
    source_file = xls_path.name
    for r in records:
        r["section"] = SECTION
        r["indicator_id"] = INDICATOR_ID
        r["indicator_title"] = INDICATOR_TITLE
        r["unit"] = UNIT
        r["source_file"] = source_file
        r["loaded_at"] = loaded_at

    out = pd.DataFrame.from_records(records, columns=DATA_COLUMNS)
    out = out.drop_duplicates(
        subset=["indicator_id", "view", "region", "year", "month", "period_type"],
        keep="last",
    ).reset_index(drop=True)
    return out


def _parse_period(period_raw) -> tuple[int, str] | None:
    """`('январь')` → (1, 'month'); `('январь-март')` → (3, 'ytd'); прочее → None."""
    if pd.isna(period_raw):
        return None
    s = str(period_raw).strip().lower()
    if "-" in s:
        last = s.split("-")[-1].strip()
        m = MONTHS.get(last)
        return (m, "ytd") if m else None
    m = MONTHS.get(s)
    return (m, "month") if m else None


def _parse_post2017(df: pd.DataFrame) -> list[dict]:
    """Формат `57824`: регион в шапке (row 2), год в шапке (row 3), период в col=1."""
    regions = df.iloc[2].ffill()
    years = df.iloc[3]
    records: list[dict] = []
    for row_idx in range(4, len(df)):
        view_raw = df.iat[row_idx, 0]
        period_raw = df.iat[row_idx, 1]
        if pd.isna(view_raw) or pd.isna(period_raw):
            continue
        view = VIEW_MAP.get(str(view_raw))
        if view is None:
            continue
        parsed_period = _parse_period(period_raw)
        if parsed_period is None:
            continue
        month_num, period_type = parsed_period

        for col in range(2, df.shape[1]):
            value = df.iat[row_idx, col]
            if pd.isna(value):
                continue
            region = clean_region(regions.iat[col])
            year_raw = years.iat[col]
            if region is None or pd.isna(year_raw):
                continue
            try:
                value_num = float(value)
                year = int(float(year_raw))
            except (TypeError, ValueError):
                continue
            records.append({
                "view": view,
                "region": region,
                "year": year,
                "month": month_num,
                "quarter": QUARTER_BY_MONTH[month_num],
                "period_type": period_type,
                "value": round(value_num, 4),
            })
    return records


def _parse_pre2017(df: pd.DataFrame) -> list[dict]:
    """Формат `43246`: год в шапке (row 2, ffill), период в шапке (row 3), регион в col=1."""
    years = df.iloc[2].ffill()
    periods = df.iloc[3]
    records: list[dict] = []
    for row_idx in range(4, len(df)):
        view_raw = df.iat[row_idx, 0]
        region_raw = df.iat[row_idx, 1]
        if pd.isna(view_raw) or pd.isna(region_raw):
            continue
        view = VIEW_MAP.get(str(view_raw))
        if view is None:
            continue
        region = clean_region(region_raw)
        if region is None:
            continue

        for col in range(2, df.shape[1]):
            value = df.iat[row_idx, col]
            if pd.isna(value):
                continue
            parsed_period = _parse_period(periods.iat[col])
            if parsed_period is None:
                continue
            month_num, period_type = parsed_period
            year_raw = years.iat[col]
            if pd.isna(year_raw):
                continue
            try:
                value_num = float(value)
                year = int(float(year_raw))
            except (TypeError, ValueError):
                continue
            records.append({
                "view": view,
                "region": region,
                "year": year,
                "month": month_num,
                "quarter": QUARTER_BY_MONTH[month_num],
                "period_type": period_type,
                "value": round(value_num, 4),
            })
    return records


__all__ = ["parse", "SECTION", "INDICATOR_ID", "INDICATOR_TITLE"]
