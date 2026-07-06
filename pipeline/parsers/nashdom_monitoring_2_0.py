"""Parser for Nashdom monitoring 2.0 Excel exports."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


SHEET_OKS = "Реестр ОКС"
SHEET_RV = "Реестр РВ"

METRICS = {
    "Общая площадь": ("total_area_m2", "м²"),
    "Жилая площадь": ("living_area_m2", "м²"),
    "Количество квартир": ("apartments_count", "шт"),
    "Количество апартаментов": ("apartments_count", "шт"),
    "Места в ДОУ": ("kindergarten_places", "мест"),
    "Места в СОШ": ("school_places", "мест"),
    "Посещения в смену в поликлиниках": ("clinic_visits_per_shift", "посещений/смена"),
    "Койки в больницах": ("hospital_beds", "коек"),
    "Машиномест": ("parking_places", "мест"),
    "Кол-во рабочих мест": ("jobs_count", "шт"),
    "Номерной фонд": ("hotel_rooms", "номеров"),
}


def _num(value) -> float | None:
    if value is None or pd.isna(value) or value == "":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _year(value) -> int:
    if value is None or pd.isna(value) or value == "":
        return 0
    numeric = pd.to_numeric(value, errors="coerce")
    if pd.notna(numeric) and 1900 <= float(numeric) <= 2100:
        return int(numeric)
    date = pd.to_datetime(value, errors="coerce")
    if pd.notna(date) and 1900 <= int(date.year) <= 2100:
        return int(date.year)
    return 0


def _source_date(path: Path) -> str:
    match = re.search(r"_(\d{8})\.xlsx$", path.name)
    if not match:
        return ""
    raw = match.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _read_sheets(path: Path) -> list[tuple[str, pd.DataFrame]]:
    out: list[tuple[str, pd.DataFrame]] = []
    with pd.ExcelFile(path) as workbook:
        for sheet in workbook.sheet_names:
            df = workbook.parse(sheet_name=sheet)
            if df.empty:
                continue
            if sheet in {SHEET_OKS, SHEET_RV} or {
                "УИН",
                "Общая площадь",
            }.issubset(set(df.columns)):
                out.append((sheet, df))
    return out


def _row_year(record: pd.Series, sheet: str) -> int:
    if sheet == SHEET_OKS:
        for col in ["Год окончания для Минстроя", "Год выдачи"]:
            if col in record:
                year = _year(record.get(col))
                if year:
                    return year
    for col in ["Год ввода по Мосстату", "Дата ввода по Мосстату", "Дата РВ по документу"]:
        if col in record:
            year = _year(record.get(col))
            if year:
                return year
    return 0


def parse(path: Path) -> pd.DataFrame:
    rows: list[dict] = []
    loaded_at = datetime.now().isoformat(timespec="seconds")
    source_date = _source_date(path)

    for sheet, df in _read_sheets(path):
        registry = "oks" if sheet == SHEET_OKS else "rv"
        for _, record in df.iterrows():
            year = _row_year(record, sheet)
            base = {
                "section": "realty",
                "indicator_id": "realty_monitoring_2_0",
                "indicator_title": "Nashdom monitoring 2.0",
                "region": "Москва",
                "year": year,
                "month": 0,
                "quarter": 0,
                "period_type": "annual" if year else "snapshot",
                "source_file": path.name,
                "loaded_at": loaded_at,
                "source_sheet": sheet,
                "registry": registry,
                "source_date": source_date,
                "uin": record.get("УИН", ""),
                "developer_inn": record.get("ИНН застройщика", record.get("ИНН Застройщика", "")),
                "developer_name": record.get("Застройщик", ""),
                "developer_group": record.get("Группа компаний", ""),
                "okrug": record.get("Округ", ""),
                "district": record.get("Район", ""),
                "object_name": record.get("Наименование объекта", ""),
                "commercial_name": record.get("Коммерческое название", record.get("Коммерческое наименование", "")),
                "object_type": record.get("Тип объекта", ""),
                "object_subtype": record.get("Подтип объекта", ""),
                "grouping": record.get("Группировка", ""),
                "funding_source": record.get("Источник финансирования", ""),
                "address": record.get("Строительный адрес", record.get("Адрес", "")),
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
