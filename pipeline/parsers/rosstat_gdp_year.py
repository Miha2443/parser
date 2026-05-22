"""Заглушка-парсер: ВВП РФ годовой (`ВВП годы (с 1995 г.).xlsx`).

Реальный парсер появится в следующей волне. Сейчас оркестратор должен
успешно отработать download → parse (skip из-за пустого DataFrame),
не падая на отсутствии модуля.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
