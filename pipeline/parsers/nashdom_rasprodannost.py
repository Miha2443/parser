"""Parser for Nashdom rasprodannost JSON exports."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


KPI_VIEWS = {
    "Объем жилищного строительства": ("construction_volume_thousand_m2", "тыс. м²"),
    "Распроданность": ("sold_pct", "%"),
    "Отношение распроданности к стройготовности": ("sold_to_readiness_pct", "%"),
    "Стройготовность": ("readiness_pct", "%"),
}

TABLE_METRICS = {
    "Объем жил. строительства": ("construction_volume_m2", "м²"),
    "Распроданность": ("sold_pct", "%"),
    "Стройготовность": ("readiness_pct", "%"),
    "Отношение распроданности  к стройготовности": ("sold_to_readiness_pct", "%"),
    "Отношение распроданности к стройготовности": ("sold_to_readiness_pct", "%"),
}

REGION_LABELS = {
    "rf": "Россия",
    "msk": "Москва",
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
    match = re.search(r"_(\d{8})\.json$", path.name)
    if not match:
        return ""
    raw = match.group(1)
    return f"{raw[:4]}-{raw[4:6]}-{raw[6:8]}"


def _region(entry: dict) -> str:
    region = str(entry.get("region") or "").strip()
    if region:
        return region
    return REGION_LABELS.get(str(entry.get("region_key") or ""), "")


def _base(entry: dict, path: Path, loaded_at: str) -> dict:
    return {
        "section": "realty",
        "indicator_id": "realty_rasprodannost",
        "indicator_title": "Nashdom rasprodannost",
        "region": _region(entry),
        "year": int(entry.get("year") or 0),
        "month": int(entry.get("month_num") or 0),
        "quarter": 0,
        "period_type": "monthly",
        "source_file": path.name,
        "loaded_at": loaded_at,
        "source_date": _source_date(path),
        "report_date": entry.get("report_date", ""),
        "report_period": entry.get("report_period", ""),
        "region_key": entry.get("region_key", ""),
        "source": entry.get("source", ""),
        "url": entry.get("url", ""),
    }


def _append_kpi(rows: list[dict], entry: dict, path: Path, loaded_at: str) -> None:
    base = _base(entry, path, loaded_at)
    for item in entry.get("kpi", []) or []:
        name = item.get("название", "")
        view, default_unit = KPI_VIEWS.get(name, (name, item.get("единица", "")))
        value = _num(item.get("значение"))
        if value is not None:
            rows.append({
                **base,
                "view": view,
                "value": value,
                "unit": default_unit,
                "entity_type": "kpi_total",
                "entity_name": name,
                "metric_label": name,
            })
        for delivery_year, raw in (item.get("по_годам") or {}).items():
            forecast_value = _num(raw)
            if forecast_value is None:
                continue
            year_match = re.match(r"(\d{4})", str(delivery_year))
            rows.append({
                **base,
                "view": view,
                "value": forecast_value,
                "unit": default_unit,
                "entity_type": "kpi_by_delivery_year",
                "entity_name": name,
                "delivery_year": int(year_match.group(1)) if year_match else 0,
                "delivery_year_label": str(delivery_year),
                "metric_label": name,
            })


def _append_tables(rows: list[dict], entry: dict, path: Path, loaded_at: str) -> None:
    base = _base(entry, path, loaded_at)
    for table_name, table_rows in (entry.get("tables") or {}).items():
        for item in table_rows or []:
            entity_name = item.get("наименование", "")
            for col, (view, unit) in TABLE_METRICS.items():
                if col not in item:
                    continue
                value = _num(item.get(col))
                if value is None:
                    continue
                rows.append({
                    **base,
                    "view": view,
                    "value": value,
                    "unit": unit,
                    "entity_type": table_name,
                    "entity_name": entity_name,
                    "metric_label": col,
                })


def parse(path: Path) -> pd.DataFrame:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict):
        data = [data]
    if not isinstance(data, list):
        return pd.DataFrame(columns=DATA_COLUMNS)

    loaded_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []
    for entry in data:
        if not isinstance(entry, dict):
            continue
        _append_kpi(rows, entry, path, loaded_at)
        _append_tables(rows, entry, path, loaded_at)

    if not rows:
        return pd.DataFrame(columns=DATA_COLUMNS)
    return pd.DataFrame(rows)
