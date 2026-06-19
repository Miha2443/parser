"""Парсер «ТОП застройщиков ЕРЗ» (erzrf.ru) — заглушка.

На вход — `TOP_EXCEL_*.xlsx` (~16 колонок: Место, Наименование, Регион,
Введено м², Перенос, %, Уточнение, Регионов, ЖК, ПТ, МД, БД, ДАП,
Рейтинг ЕРЗ, Ушёл с рынка). Реальный парсер появится в следующей волне.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
