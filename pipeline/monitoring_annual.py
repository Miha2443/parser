"""Confirmed Moscow annual IHS cells, not monthly totals or other categories."""
from datetime import date
import math
import re

import pandas as pd

from pipeline.data_access import MONTH_NAMES_RU


SHEET = "Данные с 2011 года"
IHS_LABEL = "Ввод ИЖС, тыс. кв. м"
COLUMNS = [
    "region", "year", "month", "period_type", "ИЖС",
    *["ИЖС_source_" + field for field in (
        "file", "date", "sheet", "label", "row", "column", "cell", "year_cell",
        "unit", "value", "precision_million",
    )],
]


def _text(value):
    return re.sub(r"\s+", " ", str(value)).strip().casefold() if pd.notna(value) else ""


def _number(value):
    if isinstance(value, bool):
        return None
    try:
        value = float(re.sub(r"\s+", "", value).replace(",", ".") if isinstance(value, str) else value)
    except (TypeError, ValueError, OverflowError):
        return None
    return value if math.isfinite(value) and value >= 0 else None


def read_annual_ihs(frame: pd.DataFrame, source_file: str, source_date: str | None) -> pd.DataFrame:
    """Read F years / S annual cells under the exact IHS title and calendar.

    The source explicitly labels thousand square metres. Cached annual cells
    are authoritative; missing ones are never reconstructed from G:R. Even a
    populated current-year cell is not admitted as a completed historical year.
    """
    empty = pd.DataFrame(columns=COLUMNS)
    if not source_date or frame.shape[1] < 19:
        return empty
    snapshot_year = date.fromisoformat(source_date).year
    titles = [index for index in range(len(frame)) if _text(frame.iat[index, 5]) == _text(IHS_LABEL)]
    if not titles:
        return empty
    if len(titles) != 1:
        raise ValueError("Monitoring annual IHS has multiple named blocks")
    header = titles[0] + 1
    if (header >= len(frame)
            or [_text(value) for value in frame.iloc[header, 6:18]] != MONTH_NAMES_RU
            or _text(frame.iat[header, 18]) != "год"):
        return empty

    rows, years = [], set()
    for index in range(header + 1, len(frame)):
        raw_year = frame.iat[index, 5]
        if not _text(raw_year):
            continue
        year = _number(raw_year)
        if year is None:
            break  # The next named block is not IHS, even with repeated years.
        if year != int(year) or year < 2011:
            raise ValueError("Monitoring annual IHS contains an invalid year")
        year = int(year)
        if year in years:
            raise ValueError("Monitoring annual IHS contains a duplicate year")
        years.add(year)
        if year >= snapshot_year:
            continue
        value = _number(frame.iat[index, 18])
        if value is None:
            continue
        row = index + 1
        rows.append({
            "region": "Москва", "year": year, "month": 12, "period_type": "year",
            "ИЖС": value / 1000,
            "ИЖС_source_file": source_file, "ИЖС_source_date": source_date,
            "ИЖС_source_sheet": SHEET, "ИЖС_source_label": IHS_LABEL,
            "ИЖС_source_row": row, "ИЖС_source_column": "S", "ИЖС_source_cell": f"S{row}",
            "ИЖС_source_year_cell": f"F{row}", "ИЖС_source_unit": "тыс. м²",
            "ИЖС_source_value": value, "ИЖС_source_precision_million": 0.0001,
        })
    return pd.DataFrame(rows, columns=COLUMNS)
