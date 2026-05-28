"""Парсер ВДС города Москвы по отраслям (`ВДС годы ОКВЭД2 (с 2016 г.).xlsx`).

По «Содержанию»:
  л.1 индекс физ.объёма ВРП Москвы 2005-2016 (%, старый ОКВЭД-2007)
  л.2 индекс физ.объёма ВРП Москвы 2017-2024 (%, ОКВЭД2)  → metric=vds_index
  л.3 ВДС по отраслям, текущие цены (млн руб)              → metric=vds_value
  л.4 отраслевая структура ВДС (% к итогу)                 → metric=vds_structure

Индекс по отраслям берём с листа 2 (ОКВЭД2, 2017+) полностью, а с листа 1
(ОКВЭД-2007, 2005-2016) — только за 2011+ и только по тем отраслям, чьё
название дословно совпадает с листом 2 (Строительство, Образование,
Добыча полезных ископаемых, Обрабатывающие производства) плюс сводный
«Всего». Классификации ОКВЭД-2007 и ОКВЭД2 в остальном не стыкуются по
названиям, поэтому несовпадающие старые отрасли не подмешиваем — иначе в
выборе появятся дубли-«похожие», а линии разорвутся на границе 2016/2017.
Полная история сводного индекса ВРП (с 1998 г.) также есть в индикаторе
vrp_msk (metric=vrp_index).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import parse_industry_sheet, to_frame

REGION = "Москва"
INDICATOR_ID = "vds_msk"
INDICATOR_TITLE = "Валовая добавленная стоимость по отраслям, Москва"

_LEGACY_INDEX_FROM = 2011  # с какого года тянуть старый ОКВЭД-2007 лист


def _industry(df: pd.DataFrame, *, metric: str, unit: str, source_file: str,
              include_total: bool) -> list[dict]:
    return parse_industry_sheet(
        df, region=REGION, metric=metric, unit=unit,
        indicator_id=INDICATOR_ID, indicator_title=INDICATOR_TITLE,
        source_file=source_file, include_total=include_total,
    )


def parse(path: Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path)
    sheets = set(xl.sheet_names)
    records: list[dict] = []

    if "3" in sheets:
        records += _industry(pd.read_excel(path, sheet_name="3", header=None),
                             metric="vds_value", unit="млн руб",
                             source_file=path.name, include_total=False)
    if "4" in sheets:
        records += _industry(pd.read_excel(path, sheet_name="4", header=None),
                             metric="vds_structure", unit="%",
                             source_file=path.name, include_total=False)

    idx_main: list[dict] = []
    if "2" in sheets:
        idx_main = _industry(pd.read_excel(path, sheet_name="2", header=None),
                             metric="vds_index", unit="%",
                             source_file=path.name, include_total=True)
        records += idx_main

    if "1" in sheets and idx_main:
        views_okved2 = {r["view"] for r in idx_main}
        idx_legacy = _industry(pd.read_excel(path, sheet_name="1", header=None),
                               metric="vds_index", unit="%",
                               source_file=path.name, include_total=True)
        records += [
            r for r in idx_legacy
            if r["year"] >= _LEGACY_INDEX_FROM and r["view"] in views_okved2
        ]

    return to_frame(records)

