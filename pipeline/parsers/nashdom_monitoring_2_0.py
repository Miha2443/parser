"""Парсер «Мониторинг новостроек 2.0» (наш.дом.рф) — заглушка.

Реальная нормализация будет в следующей волне (после подтверждения
структуры скачиваемого xlsx).
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
