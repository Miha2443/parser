"""Parser for ERZRF top developer Excel files."""
from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


FILE_RE = re.compile(
    r"^top_(?P<sorting>.+)_(?P<region>rf|msk)(?:_(?P<year>\d{4}))?_(?P<date>\d{8})\.xlsx$",
    re.IGNORECASE,
)

MAIN_METRICS = [
    ("Строится, м²", "construction_area_m2", "м²"),
    ("Введено, м²", "commissioned_area_m2", "м²"),
    ("Введено МКД по ДДУ за 3 года", "commissioned_ddu_3y", "м²"),
    ("Средняя площадь дома, м²", "avg_house_area_m2", "м²"),
    ("Скорость строительства, дней/дом", "construction_speed_days_per_house", "дней/дом"),
]

REGION_LABELS = {
    "rf": "Россия",
    "msk": "Москва",
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


def _first_column(columns, predicate):
    return next((col for col in columns if predicate(str(col).strip())), None)


def _metadata(path: Path) -> dict:
    match = FILE_RE.match(path.name)
    if not match:
        return {
            "sorting": "unknown",
            "region_key": "",
            "region": "",
            "year": datetime.now().year,
            "snapshot_date": "",
        }
    date = match.group("date")
    return {
        "sorting": match.group("sorting"),
        "region_key": match.group("region"),
        "region": REGION_LABELS.get(match.group("region"), match.group("region")),
        "year": int(match.group("year") or date[:4]),
        "snapshot_date": f"{date[:4]}-{date[4:6]}-{date[6:8]}",
    }


def parse(path: Path) -> pd.DataFrame:
    df = pd.read_excel(path)
    if df.empty:
        return pd.DataFrame(columns=DATA_COLUMNS)

    meta = _metadata(path)
    name_col = _first_column(df.columns, lambda c: "Наименование" in c)
    place_col = _first_column(df.columns, lambda c: c.startswith("Место"))
    metric_col, metric_view, unit = next(
        ((col, view, unit) for col, view, unit in MAIN_METRICS if col in df.columns),
        (None, "row", ""),
    )

    loaded_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []
    for _, record in df.iterrows():
        value = _num(record.get(metric_col)) if metric_col else None
        row = {
            "section": "realty",
            "indicator_id": f"realty_erzrf_top_{meta['region_key'] or 'unknown'}",
            "indicator_title": "ERZRF top developers",
            "view": metric_view,
            "region": meta["region"],
            "year": meta["year"],
            "month": 0,
            "quarter": 0,
            "period_type": "snapshot",
            "value": value,
            "unit": unit,
            "source_file": path.name,
            "loaded_at": loaded_at,
            "sorting": meta["sorting"],
            "region_key": meta["region_key"],
            "snapshot_date": meta["snapshot_date"],
            "developer_name": record.get(name_col, "") if name_col else "",
            "place": record.get(place_col, "") if place_col else "",
            "metric_column": metric_col or "",
        }
        for col in df.columns:
            row[f"raw_{col}"] = record.get(col)
        rows.append(row)

    return pd.DataFrame(rows)
