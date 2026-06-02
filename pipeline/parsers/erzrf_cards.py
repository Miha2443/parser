"""Парсер карточек застройщиков ЕРЗ (erzrf.ru/zastroyschiki/<slug>) — заглушка.

На вход — JSON-файлы из `data/raw/realty/erzrf/cards/<slug>.json`. Реальный
парсер появится в следующей волне.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
