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


# ─────────────────────────────────────────────
# Распроданность (наш.дом.рф)
# ─────────────────────────────────────────────

# ─────────────────────────────────────────────
# Мониторинг 2.0 (наш.дом.рф) — реестр ОКС + РВ
# ─────────────────────────────────────────────

MONITORING_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / "raw" / "realty" / "nashdom",
    Path(__file__).resolve().parent.parent / "nashdom",
]


@st.cache_data(show_spinner=False)
def load_monitoring_2_0() -> dict:
    """Загружает свежий monitoring_2_0_<date>.xlsx (Google Sheets export).

    Возвращает:
      'rv': DataFrame реестра РВ (введённые объекты, Год ввода по Мосстату)
      'oks': DataFrame реестра ОКС (объекты в строительстве)
      'developers': sorted list[str] — уникальные «Группа компаний» из обоих листов
      'min_year' / 'max_year': диапазон годов ввода
    """
    files = []
    for base in MONITORING_PATHS:
        if base.exists():
            files.extend(sorted(base.glob("monitoring_2_0_*.xlsx")))
    if not files:
        return {
            "rv": pd.DataFrame(),
            "oks": pd.DataFrame(),
            "developers": [],
            "min_year": None,
            "max_year": None,
        }
    latest = max(files, key=lambda p: p.stat().st_mtime)

    rv = pd.read_excel(latest, sheet_name="Реестр РВ")
    oks = pd.read_excel(latest, sheet_name="Реестр ОКС")

    # Категории площадей по правилам:
    # Жилая = Жилая площадь (если Группировка==Жилье)
    # Нежилая в жилых = Общая − Жилая (если Группировка==Жилье)
    # Нежилые здания = Общая (если Группировка==Нежилье)
    def categorize(df):
        df = df.copy()
        df["category_жилая"] = 0.0
        df["category_нежилая_в_жилых"] = 0.0
        df["category_нежилые_здания"] = 0.0
        if "Группировка" in df.columns and "Общая площадь" in df.columns:
            df["Общая площадь"] = pd.to_numeric(df["Общая площадь"], errors="coerce").fillna(0)
            df["Жилая площадь"] = pd.to_numeric(df["Жилая площадь"], errors="coerce").fillna(0)
            is_zhile = df["Группировка"] == "Жилье"
            df.loc[is_zhile, "category_жилая"] = df.loc[is_zhile, "Жилая площадь"]
            df.loc[is_zhile, "category_нежилая_в_жилых"] = (
                df.loc[is_zhile, "Общая площадь"] - df.loc[is_zhile, "Жилая площадь"]
            ).clip(lower=0)
            df.loc[~is_zhile, "category_нежилые_здания"] = df.loc[~is_zhile, "Общая площадь"]
        return df

    rv = categorize(rv)
    oks = categorize(oks)

    # Группа компаний может содержать смесь типов — приводим к str
    devs_rv = set(str(x) for x in rv["Группа компаний"].dropna().unique())
    devs_oks = set(str(x) for x in oks["Группа компаний"].dropna().unique())
    developers = sorted(devs_rv | devs_oks)

    years = rv["Год ввода по Мосстату"].dropna()
    min_year = int(years.min()) if not years.empty else None
    max_year = int(years.max()) if not years.empty else None

    return {
        "rv": rv,
        "oks": oks,
        "developers": developers,
        "min_year": min_year,
        "max_year": max_year,
    }


RASPROD_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / "raw" / "realty" / "nashdom",
    Path(__file__).resolve().parent.parent / "nashdom",
]
RASPROD_TABLE_SHEETS = ["fed_okruga", "regions", "developers", "by_dev_volume", "by_population", "by_class"]


def _parse_rasprod_number(value) -> float | None:
    """Парсит «119 346 тыс. м²» → 119346; «31%» → 31; «34 105 939» → 34105939."""
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    s = str(value).strip().replace("\xa0", " ")
    if not s or s in ("-", "—", "nan"):
        return None
    # Убираем единицы измерения
    for unit in ["тыс. м²", "млн руб", "тыс. шт", "%", "тыс."]:
        s = s.replace(unit, "")
    s = s.strip().replace(" ", "").replace(",", ".")
    try:
        return float(s)
    except ValueError:
        return None


@st.cache_data(show_spinner=False)
def load_rasprodannost() -> dict:
    """Загружает свежий rasprodannost_<date>.xlsx.

    Возвращает dict:
      'kpi': DataFrame со столбцами region_key/year/month/month_name/report_period/
             название/значение/значение_num/единица/прогноз_2026..2031+ (числовые тоже)
      'fed_okruga' / 'regions' / 'developers' / 'by_dev_volume' / 'by_population' /
      'by_class': DataFrames с region_key/year/month/section/наименование/
             Объем жил. строительства/Распроданность/Стройготовность/Отношение
             + соответствующие _num колонки
      'regions_available': list[str] — ['rf', 'msk']
      'periods': list[(year, month)] отсортированных
      'latest_period': (year, month) последний доступный
    """
    files = []
    for base in RASPROD_PATHS:
        if base.exists():
            files.extend(sorted(base.glob("rasprodannost_*.xlsx")))
    if not files:
        return {
            "kpi": pd.DataFrame(),
            **{s: pd.DataFrame() for s in RASPROD_TABLE_SHEETS},
            "regions_available": [],
            "periods": [],
            "latest_period": None,
        }
    latest = max(files, key=lambda p: p.stat().st_mtime)

    xl = pd.ExcelFile(latest)
    out: dict = {"regions_available": [], "periods": [], "latest_period": None}

    # KPI
    if "kpi" in xl.sheet_names:
        df = pd.read_excel(latest, sheet_name="kpi")
        df["значение_num"] = df["значение"].apply(_parse_rasprod_number)
        for col in df.columns:
            if col.startswith("прогноз_"):
                df[f"{col}_num"] = df[col].apply(_parse_rasprod_number)
        out["kpi"] = df
    else:
        out["kpi"] = pd.DataFrame()

    # 6 таблиц
    metric_cols = ["Объем жил. строительства", "Распроданность", "Стройготовность",
                   "Отношение распроданности  к\xa0стройготовности",
                   "Отношение распроданности к стройготовности"]
    for sheet in RASPROD_TABLE_SHEETS:
        if sheet not in xl.sheet_names:
            out[sheet] = pd.DataFrame()
            continue
        df = pd.read_excel(latest, sheet_name=sheet)
        for col in df.columns:
            if any(m in col for m in metric_cols):
                df[f"{col}_num"] = df[col].apply(_parse_rasprod_number)
        out[sheet] = df

    # Собираем мета из KPI листа (там все периоды есть)
    if not out["kpi"].empty:
        kdf = out["kpi"]
        regions = sorted(kdf["region_key"].dropna().unique().tolist())
        out["regions_available"] = regions
        periods = sorted(set(
            (int(r["year"]), int(r["month"]))
            for _, r in kdf.iterrows()
            if pd.notna(r.get("year")) and pd.notna(r.get("month"))
        ))
        out["periods"] = periods
        out["latest_period"] = periods[-1] if periods else None
    return out
