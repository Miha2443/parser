"""Парсер «Наполненность счетов эскроу» (xlsx руками) — заглушка.

Пользователь кладёт файл в `data/raw/realty/escrow_manual/`. Реальный парсер
появится после согласования структуры файла.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
