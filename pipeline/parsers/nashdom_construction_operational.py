"""Parser for the current-construction operational Nashdom snapshot."""
from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS, MONTHS


def _num(value) -> float | None:
    match = re.search(r"-?[\d\s\u00a0]+(?:[,.]\d+)?", str(value or ""))
    if not match:
        return None
    try:
        return float(match.group(0).replace("\u00a0", "").replace(" ", "").replace(",", "."))
    except ValueError:
        return None


def _period(value: str) -> tuple[int, int]:
    parts = str(value or "").strip().casefold().split()
    if len(parts) != 2:
        return 0, 0
    try:
        return int(parts[1]), MONTHS.get(parts[0], 0)
    except ValueError:
        return 0, 0


def parse(path: Path) -> pd.DataFrame:
    payload = json.loads(path.read_text(encoding="utf-8"))
    loaded_at = datetime.now().isoformat(timespec="seconds")
    rows: list[dict] = []
    for item in payload.get("construction") or []:
        year, month = _period(item.get("report_period", ""))
        value = _num(item.get("area_thousand_m2"))
        if value is None:
            continue
        rows.append({
            "section": "realty", "indicator_id": "realty_construction_operational",
            "indicator_title": "Текущее строительство", "view": f"{item.get('area_kind')}_area",
            "region": "Москва" if item.get("region_key") == "msk" else "Российская Федерация",
            "year": year, "month": month, "quarter": (month - 1) // 3 + 1 if month else 0,
            "period_type": "monthly", "value": value, "unit": "тыс. м²",
            "source_file": path.name, "loaded_at": loaded_at,
        })
    for item in payload.get("sales") or []:
        year, month = _period(item.get("report_period", ""))
        for view, raw in (item.get("metrics") or {}).items():
            value = _num(raw)
            if value is None:
                continue
            unit = "%" if view.endswith("_pct") else "руб" if view.endswith("_rub") else "тыс. м²"
            if view == "funds_million_rub":
                unit = "млн руб."
            rows.append({
                "section": "realty", "indicator_id": "realty_construction_operational",
                "indicator_title": "Реализация квартир", "view": view,
                "region": "Москва" if item.get("region_key") == "msk" else "Российская Федерация",
                "year": year, "month": month, "quarter": (month - 1) // 3 + 1 if month else 0,
                "period_type": "monthly", "value": value, "unit": unit,
                "source_file": path.name, "loaded_at": loaded_at,
            })
    return pd.DataFrame(rows) if rows else pd.DataFrame(columns=DATA_COLUMNS)
