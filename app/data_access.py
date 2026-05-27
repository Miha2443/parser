"""Загрузка Parquet-витрин с кэшированием Streamlit."""
from __future__ import annotations

from pathlib import Path

import pandas as pd
import streamlit as st

DATA_PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
DATA_DERIVED = Path(__file__).resolve().parent.parent / "data" / "derived"

MONTH_NAMES_RU = [
    "январь", "февраль", "март", "апрель", "май", "июнь",
    "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь",
]
MONTH_SHORT_RU = [
    "янв", "фев", "мар", "апр", "май", "июн",
    "июл", "авг", "сен", "окт", "ноя", "дек",
]
QUARTER_NAMES_RU = ["I квартал", "II квартал", "III квартал", "IV квартал"]
QUARTER_ROMAN = ["I", "II", "III", "IV"]


def month_label(year: int, month: int) -> str:
    """Подпись месяца на оси X: «янв 24»."""
    return f"{MONTH_SHORT_RU[int(month) - 1]} {int(year) % 100:02d}"


def quarter_label(year: int, quarter: int) -> str:
    """Подпись квартала: «I кв 24»."""
    return f"{QUARTER_ROMAN[int(quarter) - 1]} кв {int(year) % 100:02d}"


def format_thousands(value: float, digits: int = 0) -> str:
    """Русский формат: разделитель тысяч — неразрывный пробел."""
    if pd.isna(value):
        return ""
    return f"{value:,.{digits}f}".replace(",", " ")


@st.cache_data(show_spinner=False)
def load_indicator(indicator_id: str) -> pd.DataFrame:
    """Универсальный загрузчик витрины по id из реестра."""
    path = DATA_PROCESSED / f"{indicator_id}.pkl"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_pickle(path)


@st.cache_data(show_spinner=False)
def _load_derived() -> pd.DataFrame:
    """Производные committed-витрины (CSV в data/derived/), не обновляемые ETL.

    Сейчас тут:
    - vds_msk_value_2011_2015.csv — ВДС Москвы в рублях за 2011-2015 (ВРП × доля);
    - salary_2011_2012.csv — годовая зарплата Москвы/РФ за 2011-2012 (ручные данные).
    """
    if not DATA_DERIVED.exists():
        return pd.DataFrame()
    frames: list[pd.DataFrame] = []
    for csv in sorted(DATA_DERIVED.glob("*.csv")):
        df = pd.read_csv(csv)
        if "loaded_at" in df.columns:
            df["loaded_at"] = pd.to_datetime(df["loaded_at"], errors="coerce")
        frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else pd.DataFrame()


def _add_january_ytd(df: pd.DataFrame) -> pd.DataFrame:
    """YTD за январь = значение за январь (с начала года к январю = сам январь).

    В исходниках ЗП «период с начала года» начинается с «январь-февраль», т.е.
    января в ytd нет. Достраиваем его из месячных данных, чтобы он был на
    графиках «по месяцам с начала года».
    """
    jan = df[(df["period_type"] == "month") & (df["month"] == 1)].copy()
    if jan.empty:
        return df
    jan["period_type"] = "ytd"
    combined = pd.concat([df, jan], ignore_index=True)
    return combined.drop_duplicates(
        subset=["indicator_id", "view", "region", "year", "month", "period_type"],
        keep="first",
    ).reset_index(drop=True)


def load_salary() -> pd.DataFrame:
    df = load_indicator("avg_salary")
    derived = _load_derived()
    if not derived.empty:
        extra = derived[derived["indicator_id"] == "avg_salary"]
        if not extra.empty:
            df = pd.concat([df, extra], ignore_index=True)
    if df.empty:
        return df
    # Derived-CSV нацсчётов оставляет month/quarter пустыми → колонка
    # становится float; возвращаем целочисленный тип (квартал используется
    # как индекс в подписях на странице ЗП).
    for col in ("month", "quarter"):
        df[col] = df[col].astype("Int64")
    return _add_january_ytd(df)


def load_ipc() -> pd.DataFrame:
    return load_indicator("ipc")


NA_INDICATORS = ["gdp_rf", "gdp_per_capita_rf", "vrp_msk", "vds_msk", "vds_rf", "vds_msk_legacy"]


@st.cache_data(show_spinner=False)
def load_national_accounts() -> pd.DataFrame:
    """Объединённая витрина национальных счётов (ВВП/ВРП/ВДС, Москва + РФ)."""
    frames = [load_indicator(i) for i in NA_INDICATORS]
    derived = _load_derived()
    if not derived.empty:
        frames.append(derived[derived["section"] == "national_accounts"])
    frames = [f for f in frames if not f.empty]
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


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
