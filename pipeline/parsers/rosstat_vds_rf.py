"""Заглушка-парсер: ВДС РФ по отраслям (`ВДС годы ОКВЭД2 (с 2011 г.).xlsx`).

Реальный парсер появится в следующей волне.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
