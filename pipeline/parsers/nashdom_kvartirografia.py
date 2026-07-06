"""Parser for Nashdom kvartirografia Excel exports."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


SHEET_ENTITY = {
    "apartments": "apartment_type",
    "distribution": "area_range",
    "developers": "developer",
    "regions": "region_rank",
}

METRICS = {
    "количество_шт": ("apartments_count", "шт"),
    "квартиры_тыс_шт": ("apartments_thousand_count", "тыс. шт"),
    "площадь_тыс_м²": ("area_thousand_m2", "тыс. м²"),
    "доля": ("share_pct", "%"),
    "доля_1комн_%": ("one_room_share_pct", "%"),
    "доля_2комн_%": ("two_room_share_pct", "%"),
    "доля_3комн_%": ("three_room_share_pct", "%"),
    "доля_4+комн_%": ("four_plus_room_share_pct", "%"),
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


def _source_date(path: Path) -> str:
    match = re.search(r"_(\d{8})\.xlsx$", path.name)
    if not match:
        return ""
    raw = match.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _entity_name(record: pd.Series, sheet: str) -> str:
    if sheet == "apartments":
        return str(record.get("тип", "") or "")
    if sheet == "distribution":
        return str(record.get("диапазон", "") or "")
    return str(record.get("наименование", "") or "")


def parse(path: Path) -> pd.DataFrame:
    rows: list[dict] = []
    loaded_at = datetime.now().isoformat(timespec="seconds")
    source_date = _source_date(path)

    with pd.ExcelFile(path) as workbook:
        for sheet in workbook.sheet_names:
            if sheet not in SHEET_ENTITY:
                continue
            df = workbook.parse(sheet_name=sheet)
            if df.empty:
                continue
            entity_type = SHEET_ENTITY[sheet]
            for _, record in df.iterrows():
                report_date = str(record.get("report_date", "") or "")
                base = {
                    "section": "realty",
                    "indicator_id": "realty_kvartirografia",
                    "indicator_title": "Nashdom kvartirografia",
                    "region": record.get("region", ""),
                    "year": int(str(report_date)[-4:]) if re.search(r"\d{4}$", report_date) else 0,
                    "month": 0,
                    "quarter": 0,
                    "period_type": "snapshot",
                    "source_file": path.name,
                    "loaded_at": loaded_at,
                    "source_sheet": sheet,
                    "source_date": source_date,
                    "report_date": report_date,
                    "region_key": record.get("region_key", ""),
                    "entity_type": entity_type,
                    "entity_name": _entity_name(record, sheet),
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
