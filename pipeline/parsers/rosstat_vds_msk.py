"""Парсер ВДС города Москвы по отраслям (`ВДС годы ОКВЭД2 (с 2016 г.).xlsx`).

По «Содержанию»:
  л.1 индекс физ.объёма ВРП Москвы 2005-2016 (%, старый ОКВЭД-2007)
  л.2 индекс физ.объёма ВРП Москвы 2017-2024 (%, ОКВЭД2)  → metric=vds_index
  л.3 ВДС по отраслям, текущие цены (млн руб)              → metric=vds_value
  л.4 отраслевая структура ВДС (% к итогу)                 → metric=vds_structure

Индекс по отраслям берём только с листа 2 (ОКВЭД2, 2017+): на листе 1
другая классификация (ОКВЭД-2007), названия отраслей не совпадают и
сливать их нельзя. Полная история сводного индекса ВРП (с 1998 г.) и так
есть в индикаторе vrp_msk (metric=vrp_index). Строку-итог «Валовой
региональный продукт» берём как «Всего».
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import parse_industry_sheet, to_frame

REGION = "Москва"
INDICATOR_ID = "vds_msk"
INDICATOR_TITLE = "Валовая добавленная стоимость по отраслям, Москва"

# metric → список (лист, единица, включать ли итог)
_SERIES = {
    "vds_index": [("2", "%", True)],
    "vds_value": [("3", "млн руб", False)],
    "vds_structure": [("4", "%", False)],
}


def parse(path: Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path)
    records: list[dict] = []
    for metric, parts in _SERIES.items():
        for sheet, unit, with_total in parts:
            if sheet not in xl.sheet_names:
                continue
            df = pd.read_excel(path, sheet_name=sheet, header=None)
            records.extend(parse_industry_sheet(
                df, region=REGION, metric=metric, unit=unit,
                indicator_id=INDICATOR_ID, indicator_title=INDICATOR_TITLE,
                source_file=path.name, include_total=with_total,
            ))
    return to_frame(records)
