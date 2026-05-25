"""Парсер ВВП РФ, годовой (`VVP_god_s1995-*.xlsx`).

По «Содержанию»:
  л.1 ВВП в текущих ценах 1995-2011, л.2 — 2011-2025 (млрд руб) → metric=gdp_total
  л.7 индекс физ.объёма ВВП 1996-2011, л.8 — 2012-2025 (%)      → metric=gdp_index
Берём обе части (старую и новую) — перекрытие по 2011 схлопнет dedup.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import parse_single_series, to_frame

REGION = "Российская Федерация"
INDICATOR_ID = "gdp_rf"
INDICATOR_TITLE = "Валовой внутренний продукт"

# metric → (листы, единица)
_SERIES = {
    "gdp_total": (["1", "2"], "млрд руб"),
    "gdp_index": (["7", "8"], "%"),
}


def parse(path: Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path)
    records: list[dict] = []
    for metric, (sheets, unit) in _SERIES.items():
        for sheet in sheets:
            if sheet not in xl.sheet_names:
                continue
            df = pd.read_excel(path, sheet_name=sheet, header=None)
            records.extend(parse_single_series(
                df, region=REGION, metric=metric, unit=unit,
                indicator_id=INDICATOR_ID, indicator_title=INDICATOR_TITLE,
                view=metric, source_file=path.name,
            ))
    return to_frame(records)
