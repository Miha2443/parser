"""Shared Moscow renovation commissioning scope and agreed annual controls."""
from __future__ import annotations

import pandas as pd

RENOVATION_VALUES = {2017: 0.3, 2018: 0.2, 2019: 0.2, 2020: 0.4, 2021: 1.2,
                     2022: 0.8, 2023: 1.2, 2024: 1.3, 2025: 2.3}


def renovation_rows(rv):
    if rv.empty or "Инвестор для Реновации" not in rv:
        return rv.iloc[:0].copy()
    flag = rv["Инвестор для Реновации"].fillna("").astype(str).str.strip().str.casefold()
    return rv[flag.eq("да")].copy()


def renovation_living_by_year(rv, year_to):
    year_to = int(year_to) if year_to is not None else max(RENOVATION_VALUES)
    values = {year: value for year, value in RENOVATION_VALUES.items() if year <= year_to}
    rows = renovation_rows(rv)
    if not rows.empty and {"Год ввода по Мосстату", "Жилая площадь"}.issubset(rows):
        rows["year"] = pd.to_numeric(rows["Год ввода по Мосстату"], errors="coerce")
        rows["area"] = pd.to_numeric(rows["Жилая площадь"], errors="coerce").fillna(0)
        for year, part in rows.groupby("year"):
            if 2022 <= year <= year_to:
                values[int(year)] = float(part["area"].sum()) / 1_000_000
    return pd.DataFrame([{"year": year, "Реновация": value} for year, value in sorted(values.items())])
