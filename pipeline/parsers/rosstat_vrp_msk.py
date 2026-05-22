"""Заглушка-парсер: ВРП города Москвы (`ВРП с 1998 года.xlsx`).

Реальный парсер появится в следующей волне. Он будет извлекать 5 строк
показателей с каждого из двух листов (1998-2015 и 2016-2024+):
vrp_total / vrp_index / vrp_per_capita / vrp_per_capita_index / vrp_share_rf.
"""
from __future__ import annotations

from pathlib import Path

import pandas as pd

from pipeline.parsers.common import DATA_COLUMNS


def parse(path: Path) -> pd.DataFrame:
    return pd.DataFrame(columns=DATA_COLUMNS)
