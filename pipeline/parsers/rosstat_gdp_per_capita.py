"""Парсер ВВП на душу населения РФ (`VVP_na_dushu_s1995-*.xlsx`).

По «Содержанию»:
  л.1 ВВП на душу 1995-2011, л.2 — 2011-2024 (руб)         → metric=gdp_pc_total
  л.3 индекс физ.объёма на душу 1995-2011, л.4 — 2012-2024 → metric=gdp_pc_index
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import parse_single_series, to_frame

REGION = "Российская Федерация"
INDICATOR_ID = "gdp_per_capita_rf"
INDICATOR_TITLE = "Валовой внутренний продукт на душу населения"

_SERIES = {
    "gdp_pc_total": (["1", "2"], "руб"),
    "gdp_pc_index": (["3", "4"], "%"),
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
