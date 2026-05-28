"""Парсер отраслевой структуры ВДС Москвы за 2011-2015 (старый ОКВЭД-2007).

Файл «ВРП ОКВЭД 2007 (с 2004 г.)» (Росстат, statistics/accounts). Листы
«2. YYYY» — отраслевая структура ВДС субъектов РФ (% к итогу): строки —
регионы, столбцы — разделы ОКВЭД-2007 (A…P), первый столбец — итог (100).
Берём строку «г.Москва» за 2011-2015 и отдаём как metric=vds_structure.

Нужен, чтобы в блоке 5 доли Москвы тянулись с 2011, а не с 2016. Названия
секторов — в классификации ОКВЭД-2007; «Строительство», «Образование»,
«Обрабатывающие производства», «Добыча полезных ископаемых» дословно
совпадают с ОКВЭД2 и склеятся с данными 2016+, остальные старые сектора
в дашборде в выбор не попадают (там список — только ОКВЭД2, 2016+) и
оседают в «Остальные»: сумма долей по каждому году остаётся 100%.
"""
from __future__ import annotations

import re
from pathlib import Path

import pandas as pd

from pipeline.parsers.rosstat_common import _record, as_float, norm_ws, to_frame

REGION = "Москва"
INDICATOR_ID = "vds_msk_legacy"
INDICATOR_TITLE = "Отраслевая структура ВДС Москвы (ОКВЭД-2007)"
YEARS = range(2011, 2016)  # 2011-2015 включительно
_MSK_RE = re.compile(r"^г\.?москва", re.IGNORECASE)


def _sheet_for_year(xl: pd.ExcelFile, year: int) -> str | None:
    for s in xl.sheet_names:
        st = s.strip()
        if st.startswith("2.") and st.endswith(str(year)):
            return s
    return None


def _names_row(df: pd.DataFrame) -> int | None:
    """Строка с названиями отраслей — та, где встречается «Строительство»."""
    for r in range(min(10, len(df))):
        for c in range(df.shape[1]):
            if norm_ws(df.iloc[r, c]) == "Строительство":
                return r
    return None


def _moscow_row(df: pd.DataFrame) -> int | None:
    for r in range(len(df)):
        if _MSK_RE.match(re.sub(r"\s+", "", str(df.iloc[r, 0]))):
            return r
    return None


def parse(path: Path) -> pd.DataFrame:
    path = Path(path)
    xl = pd.ExcelFile(path)
    records: list[dict] = []
    for year in YEARS:
        sheet = _sheet_for_year(xl, year)
        if not sheet:
            continue
        df = pd.read_excel(path, sheet_name=sheet, header=None)
        nrow = _names_row(df)
        mrow = _moscow_row(df)
        if nrow is None or mrow is None:
            continue
        # Отрасли — все колонки с названием в строке-шапке (col 0 регион, col 1 итог — пустые).
        for c in range(df.shape[1]):
            if pd.isna(df.iloc[nrow, c]):
                continue
            name = norm_ws(df.iloc[nrow, c])
            if not name:
                continue
            value = as_float(df.iloc[mrow, c])
            if value is None:
                continue
            records.append(_record(
                indicator_id=INDICATOR_ID, indicator_title=INDICATOR_TITLE,
                view=name, region=REGION, year=year, metric="vds_structure",
                value=value, unit="%", source_file=path.name,
            ))
    return to_frame(records)
