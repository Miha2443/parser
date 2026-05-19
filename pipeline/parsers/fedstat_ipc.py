"""Парсер xls fedstat 31074 — Индексы потребительских цен (ИПЦ).

Файл выгружается двумя частями (часть1: 2011-2018, часть2: 2019-2026).

Структура листа `Данные`:
- строка 2 — года (forward-fill по 2 колонки)
- строка 3 — месяцы (forward-fill по 2 колонки)
- строка 4 — тип индекса ("К предыдущему месяцу" / "Период с начала года к соответствующему периоду предыдущего года")
- колонка 0 — название показателя
- колонка 1 — единица (процент)
- колонка 2 — регион
- колонка 3 — группа товаров ("Все товары и услуги", "Продовольственные товары", ...)
- значения с (row=5, col=4)
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

SECTION = "prices"
INDICATOR_ID = "ipc"
INDICATOR_TITLE = "Индексы потребительских цен на товары и услуги"
UNIT = "%"

PERIOD_TYPE_MAP = {
    "К предыдущему месяцу": "month_to_month",
    "Период с начала года к соответствующему периоду предыдущего года": "ytd_to_yago",
}


def parse(xls_path: Path) -> pd.DataFrame:
    df = pd.read_excel(xls_path, sheet_name="Данные", header=None)
    years = df.iloc[2].ffill()
    months = df.iloc[3].ffill()
    idx_types = df.iloc[4]
    loaded_at = datetime.now()
    source_file = xls_path.name

    records: list[dict] = []
    for row_idx in range(5, len(df)):
        region_raw = df.iat[row_idx, 2]
        view_raw = df.iat[row_idx, 3]
        if pd.isna(region_raw):
            continue
        region = REGION_CLEAN.get(str(region_raw).strip(), str(region_raw).strip())
        if region not in REGIONS_KEEP:
            continue

        view = str(view_raw).strip() if pd.notna(view_raw) else "Все товары и услуги"

        for col in range(4, df.shape[1]):
            value = df.iat[row_idx, col]
            if pd.isna(value):
                continue
            year_raw = years.iat[col]
            month_name = months.iat[col]
            idx_type_raw = idx_types.iat[col]
            if pd.isna(year_raw) or pd.isna(month_name) or pd.isna(idx_type_raw):
                continue
            month_num = MONTHS.get(str(month_name).strip().lower())
            if month_num is None:
                continue
            period_type = PERIOD_TYPE_MAP.get(str(idx_type_raw).strip())
            if period_type is None:
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
