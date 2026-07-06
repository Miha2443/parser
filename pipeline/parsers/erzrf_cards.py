"""Parser for ERZRF developer card Excel files."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


YEAR_METRIC_RE = re.compile(
    r"^(?P<label>Сдано|Перенос|Уточн)_(?P<year>\d{4})_(?P<unit>м²|%|мес)$"
)

CURRENT_METRICS = {
    "Строится_м²": ("construction_area_m2", "м²"),
    "Строится_перенос_м²": ("construction_transfer_m2", "м²"),
    "Строится_перенос_%": ("construction_transfer_pct", "%"),
    "Строится_уточн_мес": ("construction_delay_months", "мес"),
}

YEAR_LABELS = {
    "Сдано": "commissioned",
    "Перенос": "transfer",
    "Уточн": "delay",
}

UNIT_VIEW_SUFFIX = {
    "м²": "m2",
    "%": "pct",
    "мес": "months",
}


def _num(value) -> float | None:
    if value is None or pd.isna(value) or value == "" or value == "-":
        return None
    if isinstance(value, (int, float)):
        return float(value)
    text = str(value).replace("\xa0", "").replace(" ", "").replace(",", ".")
    try:
        return float(text)
    except ValueError:
        return None


def _snapshot_year(path: Path) -> int:
    match = re.search(r"_(\d{8})\.xlsx$", path.name)
    if match:
        return int(match.group(1)[:4])
    return datetime.now().year


def parse(path: Path) -> pd.DataFrame:
    try:
        df = pd.read_excel(path, sheet_name="cards")
    except ValueError:
        df = pd.read_excel(path)
    if df.empty:
        return pd.DataFrame(columns=DATA_COLUMNS)

    loaded_at = datetime.now().isoformat(timespec="seconds")
    snapshot_year = _snapshot_year(path)
    rows: list[dict] = []

    for _, record in df.iterrows():
        developer_name = record.get("name_card") or record.get("name_table") or ""
        base = {
            "section": "realty",
            "indicator_id": "realty_erzrf_cards",
            "indicator_title": "ERZRF developer cards",
            "region": "Россия",
            "month": 0,
            "quarter": 0,
            "period_type": "annual",
            "source_file": path.name,
            "loaded_at": loaded_at,
            "developer_name": developer_name,
            "name_table": record.get("name_table", ""),
            "slug": record.get("slug", ""),
            "url": record.get("url", ""),
            "place": record.get("place", ""),
            "regions_count": record.get("regions_count", ""),
            "regions_as_of": record.get("regions_as_of", ""),
        }

        for col, (view, unit) in CURRENT_METRICS.items():
            if col not in df.columns:
                continue
            rows.append({
                **base,
                "view": view,
                "year": snapshot_year,
                "value": _num(record.get(col)),
                "unit": unit,
                "metric_column": col,
            })

        for col in df.columns:
            match = YEAR_METRIC_RE.match(str(col))
            if not match:
                continue
            label = match.group("label")
            unit = match.group("unit")
            view = f"{YEAR_LABELS[label]}_{UNIT_VIEW_SUFFIX[unit]}"
            rows.append({
                **base,
                "view": view,
                "year": int(match.group("year")),
                "value": _num(record.get(col)),
                "unit": unit,
                "metric_column": col,
            })

    return pd.DataFrame(rows)
