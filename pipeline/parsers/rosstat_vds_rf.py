"""Парсер ВДС РФ по отраслям (`VDS_god_OKVED2_s2011-*.xlsx`).

По «Содержанию»:
  л.1 ВДС по отраслям, текущие цены (млрд руб)     → metric=vds_value
  л.4 индексы физ.объёма ВДС по отраслям (%)        → metric=vds_index
  л.6 структура ВДС по отраслям (% к итогу)         → metric=vds_structure
Берём разделы ОКВЭД верхнего уровня («Раздел A» …). Для индекса добавляем
строку-итог «Всего» (ВВП).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import parse_industry_sheet, to_frame

REGION = "Российская Федерация"
INDICATOR_ID = "vds_rf"
INDICATOR_TITLE = "Валовая добавленная стоимость по отраслям, РФ"

# metric → (лист, единица, включать ли строку-итог)
_SERIES = {
    "vds_value": ("1", "млрд руб", False),
    "vds_index": ("4", "%", True),
    "vds_structure": ("6", "%", False),
}


def parse(path: Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path)
    records: list[dict] = []
    for metric, (sheet, unit, with_total) in _SERIES.items():
        if sheet not in xl.sheet_names:
            continue
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        records.extend(parse_industry_sheet(
            df, region=REGION, metric=metric, unit=unit,
            indicator_id=INDICATOR_ID, indicator_title=INDICATOR_TITLE,
            source_file=path.name, include_total=with_total,
        ))
    return to_frame(records)
