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


# ─────────────────────────────────────────────
# Квартирография (наш.дом.рф)
# ─────────────────────────────────────────────

KVART_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / "raw" / "realty" / "nashdom",
    Path(__file__).resolve().parent.parent / "nashdom",
]


def _parse_kvart_number(value) -> float | None:
    """«2 439 665» → 2439665; «48» → 48; «<1» → 0.5; «-» → None."""
    if value is None:
        return None
    s = str(value).strip().replace("\xa0", "").replace(" ", "")
    if not s or s in ("-", "—"):
        return None
    if s.startswith("<"):
        return 0.5  # «< 1» — приближение для сортировки
    s = s.replace("%", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@st.cache_data(show_spinner=False)
def load_kvartirografia() -> dict:
    """Загружает свежий kvartirografia_<date>.json.

    Возвращает dict с ключами:
      'apartments' / 'distribution' / 'developers' / 'regions' — DataFrames
      'report_date': str (DD.MM.YYYY)
      'regions_available': list[str] — ['rf', 'msk'] обычно

    Каждый DataFrame имеет колонку `region_key`. Числовые значения
    преобразованы из строк («2 439 665» → 2439665.0) в колонки с суффиксом `_num`.
    Оригинальные строковые колонки сохраняются для отображения «как на сайте».
    """
    import json
    files = []
    for base in KVART_PATHS:
        if base.exists():
            files.extend(sorted(base.glob("kvartirografia_*.json")))
    if not files:
        return {
            "apartments": pd.DataFrame(),
            "distribution": pd.DataFrame(),
            "developers": pd.DataFrame(),
            "regions": pd.DataFrame(),
            "report_date": "",
            "regions_available": [],
        }
    # Самый свежий по mtime
    latest = max(files, key=lambda p: p.stat().st_mtime)
    data = json.loads(latest.read_text(encoding="utf-8"))

    apartments_rows, distribution_rows = [], []
    developers_rows, regions_rows = [], []
    report_date = ""
    region_keys: list[str] = []
    for d in data:
        rk = d.get("region_key", "")
        if rk and rk not in region_keys:
            region_keys.append(rk)
        if not report_date:
            report_date = d.get("report_date", "")
        for a in d.get("apartments", []):
            apartments_rows.append({"region_key": rk, **a})
        for x in d.get("distribution", []):
            distribution_rows.append({"region_key": rk, **x})
        for x in d.get("developers", []):
            developers_rows.append({"region_key": rk, **x})
        for x in d.get("regions", []):
            regions_rows.append({"region_key": rk, **x})

    def _df(rows, num_cols):
        df = pd.DataFrame(rows)
        for c in num_cols:
            if c in df.columns:
                df[f"{c}_num"] = df[c].apply(_parse_kvart_number)
        return df

    apartments = _df(apartments_rows, ["количество_шт", "площадь_тыс_м²"])
    distribution = _df(distribution_rows, ["доля"])
    devs_regs_cols = [
        "квартиры_тыс_шт", "площадь_тыс_м²",
        "доля_1комн_%", "доля_2комн_%", "доля_3комн_%", "доля_4+комн_%",
    ]
    developers = _df(developers_rows, devs_regs_cols)
    regions = _df(regions_rows, devs_regs_cols)

    return {
        "apartments": apartments,
        "distribution": distribution,
        "developers": developers,
        "regions": regions,
        "report_date": report_date,
        "regions_available": region_keys,
    }
