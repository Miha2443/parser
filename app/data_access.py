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


@st.cache_data(show_spinner=False, ttl=300)
def load_indicator(indicator_id: str) -> pd.DataFrame:
    """Универсальный загрузчик витрины по id из реестра."""
    path = DATA_PROCESSED / f"{indicator_id}.pkl"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_pickle(path)


@st.cache_data(show_spinner=False, ttl=300)
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


@st.cache_data(show_spinner=False, ttl=300)
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


@st.cache_data(show_spinner=False, ttl=300)
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
    per_dev_rows = []  # apartments_per_dev — точные числа на каждого
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
        # Per-dev (НОВОЕ): расплющиваем структуру apartments_per_dev
        for pd_item in d.get("apartments_per_dev", []):
            apt = pd_item.get("apartments", {}) or {}
            per_dev_rows.append({
                "region_key": rk,
                "наименование": pd_item.get("наименование", ""),
                "monitoring_name": pd_item.get("monitoring_name", ""),
                "Все_количество_шт": (apt.get("all") or {}).get("count", ""),
                "Все_площадь_тыс_м²": (apt.get("all") or {}).get("area", ""),
                "1комн_количество_шт": (apt.get("ONE") or {}).get("count", ""),
                "1комн_площадь_тыс_м²": (apt.get("ONE") or {}).get("area", ""),
                "2комн_количество_шт": (apt.get("TWO") or {}).get("count", ""),
                "2комн_площадь_тыс_м²": (apt.get("TWO") or {}).get("area", ""),
                "3комн_количество_шт": (apt.get("THREE") or {}).get("count", ""),
                "3комн_площадь_тыс_м²": (apt.get("THREE") or {}).get("area", ""),
                "4+комн_количество_шт": (apt.get("FOUR") or {}).get("count", ""),
                "4+комн_площадь_тыс_м²": (apt.get("FOUR") or {}).get("area", ""),
            })

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
    # apartments_per_dev — числовые версии всех 5 пар (Все + 1/2/3/4+ × count/area)
    per_dev_num_cols = [
        "Все_количество_шт", "Все_площадь_тыс_м²",
        "1комн_количество_шт", "1комн_площадь_тыс_м²",
        "2комн_количество_шт", "2комн_площадь_тыс_м²",
        "3комн_количество_шт", "3комн_площадь_тыс_м²",
        "4+комн_количество_шт", "4+комн_площадь_тыс_м²",
    ]
    apartments_per_dev = _df(per_dev_rows, per_dev_num_cols)

    return {
        "apartments": apartments,
        "distribution": distribution,
        "developers": developers,
        "regions": regions,
        "apartments_per_dev": apartments_per_dev,
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


@st.cache_data(show_spinner=False, ttl=300)
def load_monitoring_2_0() -> dict:
    """Загружает свежий monitoring_2_0_<date>.xlsx (Google Sheets export).

    Листы:
      «Реестр ОКС»  — объекты в строительстве
      «Реестр РВ»   — введённые объекты (2022-2026)
      «Лист4»       — введённые объекты за старые годы (2017-2021),
                      та же логика что РВ, но меньше колонок.
                      Объединяется с РВ в один DataFrame 'rv' —
                      так «Ввод с 2016» и динамика включают все годы.

    Возвращает:
      'rv': DataFrame реестра РВ + Лист4 (введённые объекты, все годы)
      'oks': DataFrame реестра ОКС (объекты в строительстве)
      'developers': sorted list[str] — уникальные «Группа компаний» из всех листов
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

    xl = pd.ExcelFile(latest)
    rv = pd.read_excel(latest, sheet_name="Реестр РВ")
    oks = pd.read_excel(latest, sheet_name="Реестр ОКС")
    rv["source_sheet"] = "Реестр РВ"

    # Третий лист со старыми годами (2017-2021). Имя может быть «Лист4»
    # или другое — берём первый лист, не являющийся ОКС/РВ, в котором
    # есть ключевые колонки реестра ввода.
    extra_sheets = [s for s in xl.sheet_names if s not in ("Реестр ОКС", "Реестр РВ")]
    for sheet in extra_sheets:
        try:
            old = pd.read_excel(latest, sheet_name=sheet)
        except Exception:  # noqa: BLE001
            continue
        required = {"Год ввода по Мосстату", "Общая площадь", "Группа компаний"}
        if not required.issubset(set(old.columns)):
            continue

        # ФИКС сдвига колонок (часть строк 2018-2019): «Год» содержит
        # название месяца («сентябрь»), а сам год уехал в «Дата ввода
        # по Мосстату». Там он в двух видах:
        #   а) число года как Excel-дата: 2019 → 1905-07-11
        #      (serial number) → год = (дата − 1899-12-30).days
        #   б) настоящая дата ввода: 2018-01-11 → год = .dt.year
        year_num = pd.to_numeric(old["Год ввода по Мосстату"], errors="coerce")
        shifted = year_num.isna() & old["Год ввода по Мосстату"].notna()
        if shifted.any() and "Дата ввода по Мосстату" in old.columns:
            excel_epoch = pd.Timestamp("1899-12-30")
            dates = pd.to_datetime(
                old.loc[shifted, "Дата ввода по Мосстату"], errors="coerce")
            serial = (dates - excel_epoch).dt.days
            # а) serial — это сам год (число 2000-2030 как Excel-дата)
            recovered = serial.where((serial >= 2000) & (serial <= 2030))
            # б) иначе — настоящая дата, берём её год
            real_year = dates.dt.year.where(
                (dates.dt.year >= 2000) & (dates.dt.year <= 2030))
            recovered = recovered.fillna(real_year)
            year_num.loc[shifted] = recovered
            # Месяц у сдвинутых строк лежит в колонке «Год»
            if "Месяц ввода по Мосстату" in old.columns:
                old.loc[shifted, "Месяц ввода по Мосстату"] = \
                    old.loc[shifted, "Год ввода по Мосстату"]
        old["Год ввода по Мосстату"] = year_num
        old["source_sheet"] = sheet
        rv = pd.concat([rv, old], ignore_index=True)

    rv["Год ввода по Мосстату"] = pd.to_numeric(
        rv["Год ввода по Мосстату"], errors="coerce")

    # Категории площадей:
    # РВ (Отрасли + Группировка) — 4 категории, без изменений:
    #   Жилое = Жилая (Отрасли=Жилые объекты AND Группировка=Жилье)
    #   МОП = Общая - Жилая (Отрасли=Жилые объекты AND Группировка=Жилье)
    #   Нежилое в жилом = Общая (Отрасли=Жилые объекты AND Группировка=Нежилье)
    #     — 1-е этажи МКД, паркинги внутри ЖК
    #   Нежилое отдельное = Общая (Отрасли != Жилые объекты)
    #     — соцобъекты, офисы, отдельно стоящие
    # ОКС (только Назначение, Подтип НЕ используется) — 3 категории,
    # но раскладка в те же 4 столбца, чтобы не ломать схему DataFrame:
    #   Жилое = Жилая (Назначение=Жилье) → category_жилое
    #   МОП = Общая - Жилая (Назначение=Жилье) → category_моп
    #   Нежилое = Общая (Назначение=Нежилье) → category_нежилое_отдельное
    #   category_нежилое_в_жилом всегда 0 (для совместимости).
    def categorize_rv(df):
        df = df.copy()
        df["category_жилое"] = 0.0
        df["category_моп"] = 0.0
        df["category_нежилое_в_жилом"] = 0.0
        df["category_нежилое_отдельное"] = 0.0
        if "Общая площадь" not in df.columns:
            return df
        df["Общая площадь"] = pd.to_numeric(df["Общая площадь"], errors="coerce").fillna(0)
        df["Жилая площадь"] = pd.to_numeric(df["Жилая площадь"], errors="coerce").fillna(0)
        is_zh_otrasl = df.get("Отрасли", "") == "Жилые объекты"
        is_zh_grp = df.get("Группировка", "") == "Жилье"
        is_nzh_grp = df.get("Группировка", "") == "Нежилье"
        # Жилое + МОП (только в жилых отраслях и жилой группировке)
        mask_zh = is_zh_otrasl & is_zh_grp
        df.loc[mask_zh, "category_жилое"] = df.loc[mask_zh, "Жилая площадь"]
        df.loc[mask_zh, "category_моп"] = (
            df.loc[mask_zh, "Общая площадь"] - df.loc[mask_zh, "Жилая площадь"]
        ).clip(lower=0)
        # Нежилое в жилом (1-е этажи, паркинги в ЖК)
        mask_nzh_in_zh = is_zh_otrasl & is_nzh_grp
        df.loc[mask_nzh_in_zh, "category_нежилое_в_жилом"] = df.loc[mask_nzh_in_zh, "Общая площадь"]
        # Нежилое отдельное (отрасли не жилые)
        mask_nzh_alone = ~is_zh_otrasl
        df.loc[mask_nzh_alone, "category_нежилое_отдельное"] = df.loc[mask_nzh_alone, "Общая площадь"]
        return df

    def categorize_oks(df):
        df = df.copy()
        df["category_жилое"] = 0.0
        df["category_моп"] = 0.0
        df["category_нежилое_в_жилом"] = 0.0     # не используется — оставлено для совместимости
        df["category_нежилое_отдельное"] = 0.0   # сюда теперь идёт ВСЁ нежилое
        if "Общая площадь" not in df.columns:
            return df
        df["Общая площадь"] = pd.to_numeric(df["Общая площадь"], errors="coerce").fillna(0)
        df["Жилая площадь"] = pd.to_numeric(df["Жилая площадь"], errors="coerce").fillna(0)
        naznachenie = df.get("Назначение", "")
        is_zh = naznachenie == "Жилье"
        df.loc[is_zh, "category_жилое"] = df.loc[is_zh, "Жилая площадь"]
        df.loc[is_zh, "category_моп"] = (
            df.loc[is_zh, "Общая площадь"] - df.loc[is_zh, "Жилая площадь"]
        ).clip(lower=0)
        is_nzh = naznachenie == "Нежилье"
        df.loc[is_nzh, "category_нежилое_отдельное"] = df.loc[is_nzh, "Общая площадь"]
        return df

    rv = categorize_rv(rv)
    oks = categorize_oks(oks)

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


# ─────────────────────────────────────────────
# ERZRF (top + cards)
# ─────────────────────────────────────────────

ERZRF_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / "raw" / "realty" / "erzrf",
    Path(__file__).resolve().parent.parent / "erzrf",
    Path(__file__).resolve().parent.parent / "nashdom",  # на всякий случай
]


def _normalize_developer_name(name: str) -> str:
    """Приводит имя застройщика к каноническому ключу для матчинга между источниками.

    Реализация вынесена в pipeline.dev_name_utils — общая для дашборда
    и парсера nashdom_checker.py.
    """
    from pipeline.dev_name_utils import normalize_developer_name
    return normalize_developer_name(name)


@st.cache_data(show_spinner=False, ttl=300)
def load_erzrf_top() -> dict:
    """Читает ERZRF TOP-файлы (5 сортировок × 2 региона).

    Файлы ожидаются в data/raw/realty/erzrf/ или ./erzrf/:
      top_obyem_stroitelstva_rf_*.xlsx
      top_obyem_stroitelstva_msk_*.xlsx
      top_obyem_vvoda_rf_*.xlsx
      top_obyem_vvoda_msk_*.xlsx
      top_nakopl_vvod_rf_*.xlsx
      top_nakopl_vvod_msk_*.xlsx
      top_potreb_kachestva_rf_*.xlsx
      top_skorost_rf_*.xlsx

    Возвращает dict[sort_key] → dict[region] → DataFrame.
    Плюс: 'all_developers' — union наименований по всем файлам,
    с нормализованным ключом для матчинга.
    """
    sortings = ["obyem_stroitelstva", "obyem_vvoda", "nakopl_vvod",
                "potreb_kachestva", "skorost"]
    regions = ["rf", "msk"]
    result: dict = {}
    all_names = set()
    # Доп. структура: per-year файлы obyem_vvoda
    # result['obyem_vvoda_by_year'] = {region: {year: DataFrame}}
    result["obyem_vvoda_by_year"] = {"rf": {}, "msk": {}}

    import re as _re
    year_pat = _re.compile(r"_(\d{4})_\d{8}\.xlsx$")

    for sorting in sortings:
        result[sorting] = {}
        for reg in regions:
            files = []
            for base in ERZRF_PATHS:
                if base.exists():
                    files.extend(sorted(base.glob(f"top_{sorting}_{reg}_*.xlsx")))
            if not files:
                continue
            # Разделяем: per-year (с _YYYY_ в имени) vs обычные
            per_year_files = []
            current_files = []
            for f in files:
                m = year_pat.search(f.name)
                if m:
                    per_year_files.append((int(m.group(1)), f))
                else:
                    current_files.append(f)
            # «Текущий» (без года в имени) — самый свежий по mtime
            if current_files:
                latest = max(current_files, key=lambda p: p.stat().st_mtime)
                try:
                    df = pd.read_excel(latest)
                    result[sorting][reg] = df
                    name_col = next((c for c in df.columns
                                     if "Наименование" in str(c)), None)
                    if name_col:
                        for v in df[name_col].dropna().unique():
                            all_names.add(str(v).strip())
                except Exception:  # noqa: BLE001
                    pass
            # Per-year: для каждого года самый свежий
            if sorting == "obyem_vvoda" and per_year_files:
                by_year: dict[int, list] = {}
                for year, f in per_year_files:
                    by_year.setdefault(year, []).append(f)
                for year, year_files in by_year.items():
                    latest_y = max(year_files, key=lambda p: p.stat().st_mtime)
                    try:
                        df = pd.read_excel(latest_y)
                        result["obyem_vvoda_by_year"][reg][year] = df
                    except Exception:  # noqa: BLE001
                        pass

    result["all_developers"] = sorted(all_names)
    return result


@st.cache_data(show_spinner=False, ttl=300)
def load_erzrf_cards() -> pd.DataFrame:
    """Читает свежий cards_*.xlsx (sheet 'cards').

    Возвращает DataFrame с колонками включая 'name_card', 'slug',
    'regions_count' и Сдано/Перенос/Уточн по годам.

    Все «Сдано_YYYY_м²» и «Перенос_YYYY_м²» в xlsx — строки с пробелами
    как разделителями тысяч («2 185 178»). Конвертим их в _num колонки.
    """
    files = []
    for base in ERZRF_PATHS:
        if base.exists():
            files.extend(sorted(base.rglob("cards_*.xlsx")))
    if not files:
        return pd.DataFrame()
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        df = pd.read_excel(latest, sheet_name="cards")
    except Exception:  # noqa: BLE001
        return pd.DataFrame()

    def parse_num(v):
        if v is None or pd.isna(v) or v == "" or v == "-":
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        s = str(v).replace("\xa0", "").replace(" ", "").replace(",", ".")
        try:
            return float(s)
        except ValueError:
            return 0.0

    for col in df.columns:
        cs = str(col)
        if (cs.startswith("Сдано_") and cs.endswith("_м²")) \
                or (cs.startswith("Перенос_") and cs.endswith("_м²")) \
                or cs.startswith("Строится"):
            df[f"{col}_num"] = df[col].apply(parse_num)
    return df


# ─────────────────────────────────────────────
# Эскроу (наполняемость счетов — пообъектный реестр Москвы)
# ─────────────────────────────────────────────

ESCROW_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / "raw" / "realty" / "escrow_manual",
    Path(__file__).resolve().parent.parent / "escrow_manual",
]


@st.cache_data(show_spinner=False, ttl=300)
def load_escrow_manual() -> pd.DataFrame:
    """Читает «Наполняемость счетов.xlsx».

    Пообъектный реестр Москвы (ЕИСЖС): 537 объектов, по каждому —
    ГК застройщика, сумма кредита, задолженность, выручка, покрытие.
    Первая строка xlsx — длинный заголовок, реальная шапка во второй
    строке → header=1.
    """
    files = []
    for base in ESCROW_PATHS:
        if base.exists():
            files.extend(sorted(base.glob("*.xlsx")))
    if not files:
        return pd.DataFrame()
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        df = pd.read_excel(latest, sheet_name="Выгрузка", header=1)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return df


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


@st.cache_data(show_spinner=False, ttl=300)
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
    # Имена колонок в xlsx могут содержать NBSP (\xa0), один или два пробела
    # подряд — поэтому матчим по prefix-логике (Распроданность/Стройготовность/
    # Объем/Отношение распроданности), а не по точному имени.
    def is_metric_col(col: str) -> bool:
        s = str(col)
        return (
            "Распроданность" in s
            or "Стройготовность" in s
            or "Объем жил" in s
            or s.startswith("Отношение распроданности")
        )

    for sheet in RASPROD_TABLE_SHEETS:
        if sheet not in xl.sheet_names:
            out[sheet] = pd.DataFrame()
            continue
        df = pd.read_excel(latest, sheet_name=sheet)
        for col in df.columns:
            if is_metric_col(col):
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
