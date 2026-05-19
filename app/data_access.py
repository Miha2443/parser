"""Загрузка Parquet-витрин с кэшированием Streamlit."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

DATA_PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"

MONTH_NAMES_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
QUARTER_NAMES_RU = ["I квартал", "II квартал", "III квартал", "IV квартал"]


@st.cache_data(show_spinner=False)
def load_salary() -> pd.DataFrame:
    path = DATA_PROCESSED / "employment_salary.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


@st.cache_data(show_spinner=False)
def load_ipc() -> pd.DataFrame:
    path = DATA_PROCESSED / "prices_ipc.parquet"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_parquet(path)


def latest_loaded_at(df: pd.DataFrame) -> str:
    if df.empty or "loaded_at" not in df.columns:
        return "—"
    ts = pd.to_datetime(df["loaded_at"]).max()
    return ts.strftime("%d.%m.%Y %H:%M")


def latest_period(df: pd.DataFrame) -> str:
    """«Последний доступный период» в формате «январь-декабрь 2024»."""
    if df.empty:
        return "—"
    months_only = df[df["period_type"].isin(["month", "month_to_month"])]
    if months_only.empty:
        return "—"
    last_year = int(months_only["year"].max())
    last_month = int(months_only[months_only["year"] == last_year]["month"].max())
    return f"январь–{MONTH_NAMES_RU[last_month - 1]} {last_year}"
