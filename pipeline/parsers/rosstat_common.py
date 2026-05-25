"""Общие помощники для парсеров национальных счётов Росстата/Мосстата.

Все эти файлы — годовые ряды на листах вида:
- строка с годами (4-значные числа по колонкам, иногда с footnote-маркером «20163)»);
- ниже строки данных.

Отраслевые листы ВДС дополнительно содержат в первых колонках код ОКВЭД
(«Раздел A», «А 01», «коды») и название отрасли.

Парсеры нормализуют всё в long-format с колонкой `metric`, отличающей
под-показатели внутри одного файла (vrp_total / vrp_index / ...).
"""
from __future__ import annotations

import re
from datetime import datetime

import pandas as pd

# Колонки витрины. Совпадают с pipeline.parsers.common.DATA_COLUMNS плюс `metric`.
NA_COLUMNS = [
    "section",
    "indicator_id",
    "indicator_title",
    "view",
    "region",
    "year",
    "month",
    "quarter",
    "period_type",
    "metric",
    "value",
    "unit",
    "source_file",
    "loaded_at",
]

_YEAR_RE = re.compile(r"(\d{4})")
_SECTION_RE = re.compile(r"Раздел", re.IGNORECASE)
_TOTAL_RE = re.compile(r"Валов\w+\s+(внутренн|региональн|добавленн)", re.IGNORECASE)


def norm_ws(s: str) -> str:
    """Неразрывные пробелы → обычные, схлопывание пробелов, trim."""
    return re.sub(r"\s+", " ", str(s).replace(" ", " ")).strip()


def as_year(v) -> int | None:
    """«2016», 2016.0, «20163)» → 2016. Прочее → None."""
    if pd.isna(v):
        return None
    m = _YEAR_RE.match(str(v).strip())
    if not m:
        return None
    y = int(m.group(1))
    return y if 1990 <= y <= 2100 else None


def as_float(v) -> float | None:
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(" ", "").replace(" ", "").replace(",", ".")
    if not s:
        return None
    try:
        return float(s)
    except ValueError:
        return None


def find_year_row(df: pd.DataFrame, max_scan: int = 8) -> tuple[int, dict[int, int]] | None:
    """Находит строку-шапку с годами. Возвращает (индекс_строки, {колонка: год})."""
    best: tuple[int, dict[int, int]] | None = None
    for r in range(min(max_scan, len(df))):
        cols: dict[int, int] = {}
        for c in range(df.shape[1]):
            y = as_year(df.iloc[r, c])
            if y is not None:
                cols[c] = y
        if len(cols) >= 3 and (best is None or len(cols) > len(best[1])):
            best = (r, cols)
    return best


def _record(**kw) -> dict:
    base = {
        "section": "national_accounts",
        "month": None,
        "quarter": None,
        "period_type": "year",
        "loaded_at": datetime.now(),
    }
    base.update(kw)
    return base


def parse_single_series(
    df: pd.DataFrame,
    *,
    region: str,
    metric: str,
    unit: str,
    indicator_id: str,
    indicator_title: str,
    view: str,
    source_file: str,
) -> list[dict]:
    """Лист с одним рядом данных (только РФ): шапка-годы + первая строка чисел под ней."""
    yr = find_year_row(df)
    if yr is None:
        return []
    yrow, year_cols = yr
    for r in range(yrow + 1, len(df)):
        vals = {c: as_float(df.iloc[r, c]) for c in year_cols}
        if sum(v is not None for v in vals.values()) < 3:
            continue
        out = []
        for c, year in year_cols.items():
            v = vals[c]
            if v is None:
                continue
            out.append(_record(
                indicator_id=indicator_id, indicator_title=indicator_title,
                view=view, region=region, year=year, metric=metric,
                value=v, unit=unit, source_file=source_file,
            ))
        return out
    return []


def parse_industry_sheet(
    df: pd.DataFrame,
    *,
    region: str,
    metric: str,
    unit: str,
    indicator_id: str,
    indicator_title: str,
    source_file: str,
    include_total: bool = False,
) -> list[dict]:
    """Отраслевой лист ВДС: строки-разделы ОКВЭД («Раздел A» …) по годам.

    Берём только разделы верхнего уровня (в коде есть «Раздел»), под-коды
    («А 01», «С (10-12)») пропускаем. `view` = название отрасли.
    Если include_total — первую строку-итог («Валовой … продукт») берём как «Всего».
    """
    yr = find_year_row(df)
    if yr is None:
        return []
    yrow, year_cols = yr
    first_year_col = min(year_cols)
    out: list[dict] = []
    total_taken = False

    for r in range(yrow + 1, len(df)):
        lead = [
            str(df.iloc[r, c]).strip()
            for c in range(first_year_col)
            if pd.notna(df.iloc[r, c]) and str(df.iloc[r, c]).strip()
        ]
        lead_join = " ".join(lead)
        is_section = any(_SECTION_RE.search(x) for x in lead)
        is_total = (
            include_total
            and not total_taken
            and not is_section
            and bool(_TOTAL_RE.search(lead_join))
            and "в том числе" not in lead_join
        )
        if not (is_section or is_total):
            continue

        if is_total:
            view = "Всего"
            total_taken = True
        else:
            # Название отрасли — самая длинная ведущая ячейка без слова «Раздел».
            cands = [x for x in lead if not _SECTION_RE.search(x)] or lead
            view = norm_ws(max(cands, key=len))

        for c, year in year_cols.items():
            v = as_float(df.iloc[r, c])
            if v is None:
                continue
            out.append(_record(
                indicator_id=indicator_id, indicator_title=indicator_title,
                view=view, region=region, year=year, metric=metric,
                value=v, unit=unit, source_file=source_file,
            ))
    return out


def to_frame(records: list[dict]) -> pd.DataFrame:
    if not records:
        return pd.DataFrame(columns=NA_COLUMNS)
    return pd.DataFrame(records)[NA_COLUMNS]
