"""Parser for manually supplied Moscow escrow account Excel exports."""
from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


SHEET_NAME = "Выгрузка"

METRICS = {
    "Жилая площадь, м2": ("living_area_m2", "м²"),
    "Сумма кредита (займа) в соответствии с условиями договора, руб.": ("credit_amount_rub", "руб"),
    "Сумма задолженности по договору кредита (займа), руб.": ("credit_debt_rub", "руб"),
    "Выручка от реализации всех площадей, руб.": ("revenue_total_rub", "руб"),
    "в т.ч. жилой площади, руб.": ("revenue_living_rub", "руб"),
    "Выручка от реализации всех площадей по эскроу, руб.": ("escrow_revenue_total_rub", "руб"),
    "Выручка от реализации жилой площади по эскроу, руб.": ("escrow_revenue_living_rub", "руб"),
    "Уровень покрытия (%)": ("coverage_pct", "%"),
    "Накопленный срок переноса ввода объекта (мес.)": ("delivery_delay_months", "мес"),
    "Распроданность жилой площади (%)": ("sold_living_area_pct", "%"),
}


def _num(value) -> float | None:
    if value is None or pd.isna(value) or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace("%", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _read(path: Path) -> pd.DataFrame:
    try:
        return pd.read_excel(path, sheet_name=SHEET_NAME, header=1)
    except ValueError:
        return pd.read_excel(path, header=1)


def _planned_date(value) -> pd.Timestamp | None:
    if value is None or pd.isna(value) or value == "":
        return None
    date = pd.to_datetime(value, errors="coerce")
    if pd.isna(date):
        return None
    return date


def parse(path: Path) -> pd.DataFrame:
    df = _read(path)
    if df.empty:
        return pd.DataFrame(columns=DATA_COLUMNS)

    rows: list[dict] = []
    loaded_at = datetime.now().isoformat(timespec="seconds")
    for _, record in df.iterrows():
        planned = _planned_date(record.get("Плановый срок ввода (текущий)"))
        base = {
            "section": "realty",
            "indicator_id": "realty_escrow_manual",
            "indicator_title": "Manual escrow account filling",
            "region": "Москва",
            "year": int(planned.year) if planned is not None else 0,
            "month": int(planned.month) if planned is not None else 0,
            "quarter": int((planned.month - 1) // 3 + 1) if planned is not None else 0,
            "period_type": "planned_delivery" if planned is not None else "snapshot",
            "source_file": path.name,
            "loaded_at": loaded_at,
            "object_id": record.get("ID объекта", ""),
            "project_name": record.get("Наименование проекта", ""),
            "address": record.get("Адрес объекта", ""),
            "developer_inn": record.get("ИНН застройщика", ""),
            "developer_name": record.get("Наименование застройщика", ""),
            "developer_group": record.get("ГК застройщика", ""),
            "credit_bank": record.get("Банк-кредитор", ""),
            "uses_escrow": record.get("Проект с использованием эскроу-счетов (да/нет)", ""),
            "sales_open": record.get("Продажи открыты (да/нет)", ""),
            "planned_delivery_date": planned.date().isoformat() if planned is not None else "",
        }
        for col, (view, unit) in METRICS.items():
            if col not in df.columns:
                continue
            value = _num(record.get(col))
            if value is None:
                continue
            rows.append({
                **base,
                "view": view,
                "value": value,
                "unit": unit,
                "metric_column": col,
            })

    if not rows:
        return pd.DataFrame(columns=DATA_COLUMNS)
    return pd.DataFrame(rows)
