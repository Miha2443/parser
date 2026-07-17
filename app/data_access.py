"""Загрузка Parquet-витрин с кэшированием Streamlit."""
from __future__ import annotations

import os
import json
from collections.abc import Callable
from pathlib import Path

import pandas as pd
import streamlit as st

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_PROCESSED = Path(__file__).resolve().parent.parent / "data" / "processed"
DATA_DERIVED = Path(__file__).resolve().parent.parent / "data" / "derived"
DATA_MARTS_REALTY = PROJECT_ROOT / "data" / "marts" / "realty"
_REALTY_MART_MANIFEST_CACHE: dict[str, tuple[tuple[int, int], dict]] = {}

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


def _realty_marts_enabled() -> bool:
    return os.environ.get("PARSER_USE_REALTY_MARTS", "1").strip().lower() not in {
        "0", "false", "no", "off",
    }


def _realty_marts_required() -> bool:
    return os.environ.get("PARSER_REQUIRE_REALTY_MARTS", "0").strip().lower() in {
        "1", "true", "yes", "on",
    }


def _raw_files(paths: list[Path], patterns: list[str], *, recursive: bool = False) -> list[Path]:
    files: list[Path] = []
    for base in paths:
        if not base.exists():
            continue
        for pattern in patterns:
            iterator = base.rglob(pattern) if recursive else base.glob(pattern)
            files.extend(sorted(p for p in iterator if p.is_file()))
    return files


def _realty_mart_manifest(required: bool = False) -> dict | None:
    manifest_path = DATA_MARTS_REALTY / "manifest.json"
    if not manifest_path.exists():
        if required:
            raise FileNotFoundError(f"missing realty mart manifest: {manifest_path}")
        return None
    try:
        st = manifest_path.stat()
        cache_key = str(manifest_path.resolve())
        stat_key = (
            int(st.st_size),
            int(getattr(st, "st_mtime_ns", int(st.st_mtime * 1_000_000_000))),
        )
        cached = _REALTY_MART_MANIFEST_CACHE.get(cache_key)
        if cached and cached[0] == stat_key:
            return cached[1]
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        if required:
            raise RuntimeError(f"cannot load realty mart manifest: {exc}") from exc
        return None
    marts = manifest.get("marts") if isinstance(manifest, dict) else None
    if not isinstance(marts, dict):
        if required:
            raise RuntimeError("invalid realty mart manifest: missing marts object")
        return None
    _REALTY_MART_MANIFEST_CACHE[cache_key] = (stat_key, manifest)
    return manifest


def _realty_mart_manifest_entry(name: str, *, required: bool = False) -> dict | None:
    manifest = _realty_mart_manifest(required=required)
    if manifest is None:
        return None
    marts = manifest["marts"]
    entry = marts.get(name)
    if not isinstance(entry, dict):
        if required:
            raise KeyError(f"missing realty mart manifest entry: {name}")
        return None
    return entry if isinstance(entry, dict) else None


def latest_realty_mart_source_date(name: str) -> str:
    """Return DD.MM.YYYY for the latest source recorded in the realty mart manifest."""
    entry = _realty_mart_manifest_entry(name)
    if not entry:
        return ""
    latest = None
    for source in entry.get("sources") or []:
        if not isinstance(source, dict):
            continue
        ts = pd.to_datetime(source.get("mtime"), errors="coerce")
        if pd.isna(ts):
            continue
        if latest is None or ts > latest:
            latest = ts
    return latest.strftime("%d.%m.%Y") if latest is not None else ""


def latest_raw_source_date(*patterns: str, base: Path | None = None) -> str:
    """Return DD.MM.YYYY for the newest raw realty file matching any pattern."""
    from datetime import datetime as _dt

    root = base or PROJECT_ROOT / "data" / "raw" / "realty"
    if not root.exists():
        return ""
    files: list[Path] = []
    for pattern in patterns:
        files.extend(p for p in root.rglob(pattern) if p.is_file())
    if not files:
        return ""
    latest = max(files, key=lambda p: p.stat().st_mtime)
    return _dt.fromtimestamp(latest.stat().st_mtime).strftime("%d.%m.%Y")


def _load_realty_mart(name: str, raw_files: list[Path] | Callable[[], list[Path]] | None):
    """Read a prebuilt realty mart when it is present and not older than raw."""
    required = _realty_marts_required()
    if not _realty_marts_enabled():
        if required:
            raise RuntimeError("realty marts are required but disabled")
        return None
    path = DATA_MARTS_REALTY / f"{name}.pkl"
    if not path.exists():
        if required:
            raise FileNotFoundError(f"missing realty mart: {path}")
        return None
    try:
        manifest_entry = _realty_mart_manifest_entry(name, required=required)
        if manifest_entry is None:
            return None
        if manifest_entry and manifest_entry.get("error"):
            if required:
                raise RuntimeError(f"manifest marks realty mart as error: {name}")
            return None
        mart_mtime = path.stat().st_mtime
        live_raw_files = raw_files() if callable(raw_files) and required else (
            None if callable(raw_files) else raw_files
        )
        if live_raw_files and any(p.exists() and p.stat().st_mtime > mart_mtime for p in live_raw_files):
            if required:
                raise RuntimeError(f"stale realty mart: {name}")
            return None
        return pd.read_pickle(path)
    except Exception as exc:  # noqa: BLE001
        if required:
            raise RuntimeError(f"cannot load realty mart {name}: {exc}") from exc
        return None


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


KVART_REGION_NAME_MARKERS = (
    " область",
    " край",
    " республика",
    " автоном",
    " округ",
    "город ",
    "г.",
    "санкт-петербург",
    "москва",
)


def _kvart_empty() -> dict:
    return {
        "apartments": pd.DataFrame(),
        "distribution": pd.DataFrame(),
        "developers": pd.DataFrame(),
        "regions": pd.DataFrame(),
        "apartments_per_dev": pd.DataFrame(),
        "report_date": "",
        "regions_available": [],
    }


def _kvart_df(rows: list[dict], num_cols: list[str]) -> pd.DataFrame:
    df = pd.DataFrame(rows)
    for c in num_cols:
        if c in df.columns:
            df[f"{c}_num"] = df[c].apply(_parse_kvart_number)
    return df


def _kvart_normalize_frames(payload: dict) -> dict:
    devs_regs_cols = [
        "квартиры_тыс_шт", "площадь_тыс_м²",
        "доля_1комн_%", "доля_2комн_%", "доля_3комн_%", "доля_4+комн_%",
    ]
    per_dev_num_cols = [
        "Все_количество_шт", "Все_площадь_тыс_м²",
        "1комн_количество_шт", "1комн_площадь_тыс_м²",
        "2комн_количество_шт", "2комн_площадь_тыс_м²",
        "3комн_количество_шт", "3комн_площадь_тыс_м²",
        "4+комн_количество_шт", "4+комн_площадь_тыс_м²",
    ]
    out = {
        "apartments": _kvart_df(list(payload.get("apartments", [])), ["количество_шт", "площадь_тыс_м²"]),
        "distribution": _kvart_df(list(payload.get("distribution", [])), ["доля"]),
        "developers": _kvart_df(list(payload.get("developers", [])), devs_regs_cols),
        "regions": _kvart_df(list(payload.get("regions", [])), devs_regs_cols),
        "apartments_per_dev": _kvart_df(list(payload.get("apartments_per_dev", [])), per_dev_num_cols),
        "report_date": str(payload.get("report_date") or ""),
        "regions_available": list(payload.get("regions_available") or []),
    }
    if not out["regions_available"]:
        region_keys: list[str] = []
        for key in ("apartments", "distribution", "developers", "regions", "apartments_per_dev"):
            frame = out[key]
            if isinstance(frame, pd.DataFrame) and "region_key" in frame.columns:
                for rk in frame["region_key"].dropna().astype(str):
                    if rk and rk not in region_keys:
                        region_keys.append(rk)
        out["regions_available"] = region_keys
    if not out["report_date"]:
        for key in ("apartments", "distribution", "developers", "regions", "apartments_per_dev"):
            frame = out[key]
            if isinstance(frame, pd.DataFrame) and "report_date" in frame.columns and not frame.empty:
                out["report_date"] = str(frame["report_date"].dropna().astype(str).iloc[0])
                break
    return out


def _kvart_name_looks_like_region(name: object) -> bool:
    s = str(name or "").strip().casefold()
    return bool(s) and any(marker in f" {s}" for marker in KVART_REGION_NAME_MARKERS)


def _kvart_developers_look_like_regions(frame: pd.DataFrame) -> bool:
    if frame is None or frame.empty or "наименование" not in frame.columns:
        return False
    names = frame["наименование"].dropna().astype(str).head(12).tolist()
    if len(names) < 3:
        return False
    hits = sum(1 for name in names if _kvart_name_looks_like_region(name))
    return hits >= max(3, len(names) // 2)


def _kvart_payload_is_valid(payload: dict) -> bool:
    if not isinstance(payload, dict):
        return False
    devs = payload.get("developers")
    regions_available = set(map(str, payload.get("regions_available") or []))
    if not isinstance(devs, pd.DataFrame) or devs.empty:
        return False
    if {"rf", "msk"} - regions_available:
        return False
    if "region_key" not in devs.columns:
        return False
    msk_devs = devs[devs["region_key"].astype(str) == "msk"]
    if msk_devs.empty or _kvart_developers_look_like_regions(msk_devs):
        return False
    return True


def _load_kvartirografia_json(path: Path) -> dict:
    import json
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return _kvart_empty()
    if not isinstance(data, list):
        return _kvart_empty()

    payload = {
        "apartments": [],
        "distribution": [],
        "developers": [],
        "regions": [],
        "apartments_per_dev": [],
        "report_date": "",
        "regions_available": [],
    }
    for item in data:
        if not isinstance(item, dict):
            continue
        rk = str(item.get("region_key") or "")
        if rk and rk not in payload["regions_available"]:
            payload["regions_available"].append(rk)
        if not payload["report_date"]:
            payload["report_date"] = item.get("report_date", "")
        for key in ("apartments", "distribution", "developers", "regions"):
            for row in item.get(key, []) or []:
                if isinstance(row, dict):
                    payload[key].append({"region_key": rk, **row})
        for pd_item in item.get("apartments_per_dev", []) or []:
            if not isinstance(pd_item, dict):
                continue
            apt = pd_item.get("apartments", {}) or {}
            payload["apartments_per_dev"].append({
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
    return _kvart_normalize_frames(payload)


def _load_kvartirografia_xlsx(path: Path) -> dict:
    payload = {
        "apartments": [],
        "distribution": [],
        "developers": [],
        "regions": [],
        "apartments_per_dev": [],
        "report_date": "",
        "regions_available": [],
    }
    try:
        xl = pd.ExcelFile(path)
    except Exception:  # noqa: BLE001
        return _kvart_empty()
    for sheet in ("apartments", "distribution", "developers", "regions", "apartments_per_dev"):
        if sheet not in xl.sheet_names:
            continue
        try:
            df = pd.read_excel(path, sheet_name=sheet)
        except Exception:  # noqa: BLE001
            continue
        if sheet == "apartments_per_dev" and "источник_имя_monitoring" in df.columns:
            df = df.rename(columns={"источник_имя_monitoring": "monitoring_name"})
        rows = df.where(pd.notna(df), "").to_dict("records")
        payload[sheet] = rows
        if "region_key" in df.columns:
            for rk in df["region_key"].dropna().astype(str):
                if rk and rk not in payload["regions_available"]:
                    payload["regions_available"].append(rk)
        if not payload["report_date"] and "report_date" in df.columns and not df.empty:
            payload["report_date"] = str(df["report_date"].dropna().astype(str).iloc[0])
    return _kvart_normalize_frames(payload)


def _load_kvartirografia_file(path: Path) -> dict:
    if path.suffix.lower() == ".xlsx":
        return _load_kvartirografia_xlsx(path)
    return _load_kvartirografia_json(path)


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
    raw_files = lambda: _raw_files(KVART_PATHS, ["kvartirografia_*.json", "kvartirografia_*.xlsx"])
    mart = _load_realty_mart("kvartirografia", raw_files)
    if mart is not None and _kvart_payload_is_valid(mart):
        return mart
    files = sorted(raw_files(), key=lambda p: p.stat().st_mtime, reverse=True)
    for path in files:
        payload = _load_kvartirografia_file(path)
        if _kvart_payload_is_valid(payload):
            return payload
    return _kvart_empty()


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
      «Реестр РВ»   — введённые объекты, все строки листа
      «Лист4»       — старые годы, добавляется к «Реестр РВ»

    Возвращает:
      'rv': DataFrame реестра РВ
      'oks': DataFrame реестра ОКС
      'developers': sorted list[str] — уникальные «Группа компаний» из всех листов
      'min_year' / 'max_year': диапазон годов ввода
    """
    raw_files = lambda: _raw_files(MONITORING_PATHS, ["monitoring_2_0_*.xlsx"])
    mart = _load_realty_mart("monitoring_2_0", raw_files)
    if mart is not None:
        return mart
    files = raw_files()
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
        core_cols = [
            c for c in ("УИН", "№ РВ", "Дата ввода по Мосстату", "Год ввода по Мосстату", "Общая площадь")
            if c in old.columns
        ]
        if core_cols:
            old = old[old[core_cols].notna().any(axis=1)].copy()

        # ФИКС сдвига колонок (часть строк 2018-2019): «Год» содержит
        # название месяца («сентябрь»), а сам год уехал в «Дата ввода
        # по Мосстату». Там он в двух видах:
        #   а) число года как Excel-дата: 2019 → 1905-07-11
        #      (serial number) → год = (дата − 1899-12-30).days
        #   б) настоящая дата ввода: 2018-01-11 → год = .dt.year
        year_num = pd.to_numeric(old["Год ввода по Мосстату"], errors="coerce")
        year_raw = old["Год ввода по Мосстату"]
        shifted = (
            (year_num.isna() | ~year_num.between(2000, 2030))
            & year_raw.notna()
        )
        if shifted.any() and "Дата ввода по Мосстату" in old.columns:
            excel_epoch = pd.Timestamp("1899-12-30")
            dates = pd.to_datetime(old.loc[shifted, "Дата ввода по Мосстату"], errors="coerce")
            serial = (dates - excel_epoch).dt.days
            # а) serial — это сам год (число 2000-2030 как Excel-дата)
            recovered = serial.where((serial >= 2000) & (serial <= 2030))
            # б) иначе — настоящая дата, берём её год
            real_year = dates.dt.year.where((dates.dt.year >= 2000) & (dates.dt.year <= 2030))
            recovered = recovered.fillna(real_year)
            year_num.loc[shifted] = recovered
            # Месяц у сдвинутых строк лежит в колонке «Год»
            if "Месяц ввода по Мосстату" in old.columns:
                month_values = year_raw.loc[shifted].where(pd.to_numeric(year_raw.loc[shifted], errors="coerce").isna())
                old.loc[shifted, "Месяц ввода по Мосстату"] = month_values.combine_first(
                    old.loc[shifted, "Месяц ввода по Мосстату"]
                )
        old["Год ввода по Мосстату"] = year_num
        old["source_sheet"] = sheet
        rv = pd.concat([rv, old], ignore_index=True)

    rv["Год ввода по Мосстату"] = pd.to_numeric(
        rv["Год ввода по Мосстату"], errors="coerce")
    rv = rv[rv["Год ввода по Мосстату"].notna()].copy()
    if "Группа компаний" in rv.columns:
        rv = rv[~rv["Группа компаний"].astype(str).str.strip().str.lower().eq("false")].copy()

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
    # Streamlit при запуске страницы из app/pages/ добавляет в sys.path
    # только app/, поэтому корневой пакет pipeline не виден. Добиваем sys.path
    # корнем проекта (C:\v6\), один раз за процесс.
    import sys
    from pathlib import Path as _Path
    root = str(_Path(__file__).resolve().parent.parent)
    if root not in sys.path:
        sys.path.insert(0, root)
    from pipeline.dev_name_utils import normalize_developer_name
    return normalize_developer_name(name)


def _erz_name_col(df: pd.DataFrame) -> str | None:
    return next((c for c in df.columns if "Наименование" in str(c)), None)


def _erz_has_commissioned_col(df: pd.DataFrame) -> bool:
    return any("Введено" in str(c) for c in df.columns)


def _erz_nakopl_valid(df: pd.DataFrame | None) -> bool:
    return isinstance(df, pd.DataFrame) and not df.empty and _erz_has_commissioned_col(df) and _erz_name_col(df) is not None


def _load_erz_nakopl_fallback_frame() -> pd.DataFrame:
    files = _raw_files(ERZRF_PATHS, ["TOP_EXCEL*.xlsx"], recursive=True)
    if not files:
        return pd.DataFrame()
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        df = pd.read_excel(latest)
    except Exception:  # noqa: BLE001
        return pd.DataFrame()
    return df if _erz_nakopl_valid(df) else pd.DataFrame()


def _refresh_erz_all_developers(result: dict) -> None:
    names = set(map(str, result.get("all_developers", []) or []))
    for value in result.values():
        if not isinstance(value, dict):
            continue
        for frame in value.values():
            if not isinstance(frame, pd.DataFrame) or frame.empty:
                continue
            name_col = _erz_name_col(frame)
            if not name_col:
                continue
            for raw in frame[name_col].dropna().unique():
                name = str(raw).strip()
                if name:
                    names.add(name)
    result["all_developers"] = sorted(names)


def _apply_erz_nakopl_fallback(result: dict) -> dict:
    nakopl = result.setdefault("nakopl_vvod", {})
    if _erz_nakopl_valid(nakopl.get("rf")) and _erz_nakopl_valid(nakopl.get("msk")):
        return result
    fallback = _load_erz_nakopl_fallback_frame()
    if fallback.empty:
        return result

    if not _erz_nakopl_valid(nakopl.get("rf")):
        nakopl["rf"] = fallback.copy()

    if not _erz_nakopl_valid(nakopl.get("msk")):
        name_col = _erz_name_col(fallback)
        if name_col:
            mask = fallback[name_col].astype(str).str.contains("Москва", case=False, na=False)
            msk = fallback[mask].copy()
            nakopl["msk"] = msk if not msk.empty else fallback.copy()
        else:
            nakopl["msk"] = fallback.copy()

    _refresh_erz_all_developers(result)
    return result


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
    mart = _load_realty_mart(
        "erzrf_top",
        lambda: _raw_files(ERZRF_PATHS, ["top_*.xlsx", "TOP_EXCEL*.xlsx", "top_developers_*.json"], recursive=True),
    )
    if mart is not None:
        return _apply_erz_nakopl_fallback(mart)

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
    return _apply_erz_nakopl_fallback(result)


@st.cache_data(show_spinner=False, ttl=300)
def load_erzrf_cards() -> pd.DataFrame:
    """Читает свежий cards_*.xlsx (sheet 'cards').

    Возвращает DataFrame с колонками включая 'name_card', 'slug',
    'regions_count' и Сдано/Перенос/Уточн по годам.

    Все «Сдано_YYYY_м²» и «Перенос_YYYY_м²» в xlsx — строки с пробелами
    как разделителями тысяч («2 185 178»). Конвертим их в _num колонки.
    """
    raw_files = lambda: _raw_files(ERZRF_PATHS, ["cards_*.xlsx"], recursive=True)
    mart = _load_realty_mart("erzrf_cards", raw_files)
    if mart is not None:
        return mart
    files = raw_files()
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
    raw_files = lambda: _raw_files(ESCROW_PATHS, ["*.xlsx"])
    mart = _load_realty_mart("escrow_manual", raw_files)
    if mart is not None:
        return mart
    files = raw_files()
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
    raw_files = lambda: _raw_files(RASPROD_PATHS, ["rasprodannost_*.xlsx"])
    mart = _load_realty_mart("rasprodannost", raw_files)
    if mart is not None:
        return mart
    files = raw_files()
    if not files:
        return {
            "kpi": pd.DataFrame(),
            **{s: pd.DataFrame() for s in RASPROD_TABLE_SHEETS},
            "regions_available": [],
            "periods": [],
            "latest_period": None,
        }
    # Откат к более старому файлу если самый свежий битый: rasprod-чекер
    # мог быть убит по watchdog'у посреди скачивания и оставить
    # огрызок xlsx — pandas роняет всё приложение с BadZipFile.
    import zipfile
    files_sorted = sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)
    latest = None
    xl = None
    for candidate in files_sorted:
        try:
            if candidate.stat().st_size < 1024:
                raise zipfile.BadZipFile(f"too small: {candidate.stat().st_size}b")
            xl = pd.ExcelFile(candidate)
            latest = candidate
            break
        except (zipfile.BadZipFile, OSError, ValueError) as exc:
            st.warning(
                f"Пропускаю битый файл {candidate.name}: {exc}. "
                f"Откатываюсь на предыдущий."
            )
    if xl is None or latest is None:
        st.error("Все rasprodannost_*.xlsx битые — нечего показать.")
        return {
            "kpi": pd.DataFrame(),
            **{s: pd.DataFrame() for s in RASPROD_TABLE_SHEETS},
            "regions_available": [],
            "periods": [],
            "latest_period": None,
        }
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


# ─────────────────────────────────────────────
# Ввод недвижимости — статичные справочники (vvod.xlsx, Stroi_111) + ЕМИСС 34118
# ─────────────────────────────────────────────

VVOD_PATHS = [
    Path(__file__).resolve().parent.parent / "data" / "raw" / "realty" / "vvod",
    Path(__file__).resolve().parent.parent / "vvod",
]


def _vvod_dir() -> Path | None:
    for base in VVOD_PATHS:
        if base.exists():
            return base
    return None


def _to_float(v) -> float:
    """«2 185 178» / «6,8» / «<1» / NaN → float (NaN если не парсится)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return float("nan")
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).replace("\xa0", "").replace(" ", "").replace(",", ".").strip()
    s = s.lstrip("<>").strip()
    try:
        return float(s)
    except ValueError:
        return float("nan")


def _extract_year(v) -> int | None:
    """Достаёт год из ячейки-заголовка, в т.ч. со сноской («20221)» → 2022)."""
    if v is None or (isinstance(v, float) and pd.isna(v)):
        return None
    if isinstance(v, (int, float)) and not pd.isna(v):
        y = int(v)
        return y if 2000 <= y <= 2030 else None
    import re as _re
    m = _re.search(r"(20\d{2})", str(v))
    if m:
        y = int(m.group(1))
        return y if 2000 <= y <= 2030 else None
    return None


def _year_rows(df: pd.DataFrame, ycol: int, lo: int = 2011, hi: int = 2026,
               start: int = 0, stop: int | None = None) -> list[int]:
    """Индексы строк, где df[ycol] — целый год в [lo, hi]. Для split-блоков
    в одном листе (нежилая: №1 и №3) ограничиваем диапазоном [start, stop)."""
    out = []
    stop = len(df) if stop is None else stop
    for i in range(start, stop):
        y = _to_float(df.iat[i, ycol])
        if not pd.isna(y) and lo <= int(y) <= hi and float(int(y)) == y:
            out.append(i)
    return out


@st.cache_data(show_spinner=False, ttl=300)
def load_vvod_static() -> dict:
    """Статичные справочники ввода недвижимости (млн м²).

    Возвращает tidy-DataFrame'ы:
      'msk_total'      : year, жильё, нежильё            (vvod «ввод недвижимости»)
      'rf_total'       : year, жильё, нежильё, общая     (Stroi_111)
      'msk_residential': year, МКД, ИЖС                  (vvod «жилая»)
      'msk_nonres'     : year, нежильё, нежилые_в_жилье, общая (vvod «нежилая» №1)
      'msk_nonres_branches': year, офисы, соц, пром, гостиницы (vvod «нежилая» №3)
    """
    empty = {k: pd.DataFrame() for k in
             ("msk_total", "rf_total", "msk_residential",
              "msk_nonres", "msk_nonres_branches")}
    base = _vvod_dir()
    if base is None:
        return empty
    mart = _load_realty_mart("vvod_static", lambda: _raw_files([base], ["*.xls*", "*.txt"]))
    if mart is not None:
        return mart
    vvod_path = base / "vvod.xlsx"
    stroi_path = base / "Stroi_111_2025.xls"
    out = dict(empty)

    if vvod_path.exists():
        xl = pd.ExcelFile(vvod_path)
        # имена листов могут быть обрезаны Excel'ем до 31 символа — матчим по префиксу
        def _sheet(prefix: str) -> str | None:
            return next((s for s in xl.sheet_names if s.startswith(prefix)), None)

        s_total = _sheet("ввод недвижимости")
        if s_total:
            df = pd.read_excel(vvod_path, sheet_name=s_total, header=None)
            rows = _year_rows(df, 0)
            out["msk_total"] = pd.DataFrame({
                "year": [int(_to_float(df.iat[i, 0])) for i in rows],
                "жильё": [_to_float(df.iat[i, 1]) for i in rows],
                "нежильё": [_to_float(df.iat[i, 2]) for i in rows],
            })

        s_res = _sheet("жилая недвижимость")
        if s_res:
            df = pd.read_excel(vvod_path, sheet_name=s_res, header=None)
            rows = _year_rows(df, 0)
            out["msk_residential"] = pd.DataFrame({
                "year": [int(_to_float(df.iat[i, 0])) for i in rows],
                "МКД": [_to_float(df.iat[i, 1]) for i in rows],
                "ИЖС": [_to_float(df.iat[i, 2]) for i in rows],
            })

        s_non = _sheet("нежилая недвижимость")
        if s_non:
            df = pd.read_excel(vvod_path, sheet_name=s_non, header=None)
            # лист содержит 2 блока: №1 (нежильё/нежилые-в-жилье/общая) и
            # №3 (офисы/соц/пром/гостиницы), разделённые строкой-маркером «№3».
            n3 = None
            for i in range(len(df)):
                if str(df.iat[i, 0]).strip() == "№3":
                    n3 = i
                    break
            stop1 = n3 if n3 is not None else len(df)
            rows1 = _year_rows(df, 0, start=0, stop=stop1)
            out["msk_nonres"] = pd.DataFrame({
                "year": [int(_to_float(df.iat[i, 0])) for i in rows1],
                "нежильё": [_to_float(df.iat[i, 1]) for i in rows1],
                "нежилые_в_жилье": [_to_float(df.iat[i, 2]) for i in rows1],
                "общая": [_to_float(df.iat[i, 3]) for i in rows1],
            })
            if n3 is not None:
                rows3 = _year_rows(df, 0, start=n3, stop=len(df))
                out["msk_nonres_branches"] = pd.DataFrame({
                    "year": [int(_to_float(df.iat[i, 0])) for i in rows3],
                    "офисы": [_to_float(df.iat[i, 1]) for i in rows3],
                    "соц": [_to_float(df.iat[i, 2]) for i in rows3],
                    "пром": [_to_float(df.iat[i, 3]) for i in rows3],
                    "гостиницы": [_to_float(df.iat[i, 4]) for i in rows3],
                })

    if stroi_path.exists():
        df = pd.read_excel(stroi_path, sheet_name=0, header=None)
        # ищем строку-заголовок с годами (в т.ч. со сносками «20221)»)
        yrow = None
        for i in range(min(6, len(df))):
            yrs = sum(1 for j in range(1, df.shape[1])
                      if _extract_year(df.iat[i, j]) is not None)
            if yrs >= 10:
                yrow = i
                break
        if yrow is not None:
            year_cols = {}
            for j in range(1, df.shape[1]):
                y = _extract_year(df.iat[yrow, j])
                if y is not None:
                    year_cols[y] = j

            def _row_by_label(needle: str) -> int | None:
                for i in range(len(df)):
                    if needle in str(df.iat[i, 0]).lower():
                        return i
                return None

            r_total = _row_by_label("общая площадь зданий")
            # «жилого назначения» / «нежилого назначения» встречаются дважды
            # (количество зданий и площадь) — берём те, что НИЖЕ строки «Общая площадь»
            r_zh = r_nzh = None
            for i in range(len(df)):
                lab = str(df.iat[i, 0]).lower()
                if r_total is not None and i > r_total:
                    if r_zh is None and "жилого назначения" in lab and "нежилого" not in lab:
                        r_zh = i
                    elif r_nzh is None and "нежилого назначения" in lab:
                        r_nzh = i
            yrs = sorted(y for y in year_cols if 2011 <= y <= 2025)
            out["rf_total"] = pd.DataFrame({
                "year": yrs,
                "жильё": [_to_float(df.iat[r_zh, year_cols[y]]) if r_zh else float("nan") for y in yrs],
                "нежильё": [_to_float(df.iat[r_nzh, year_cols[y]]) if r_nzh else float("nan") for y in yrs],
                "общая": [_to_float(df.iat[r_total, year_cols[y]]) if r_total else float("nan") for y in yrs],
            })

    return out


def _parse_emiss_34118_file(path: Path) -> dict:
    """Парсит один ЕМИСС-34118 .xls → {year: {'МКД': млн м², 'ИЖС': млн м²}} для РФ.

    Объединяет 2 РФ-строки («Российская Федерация» 2015-2022 +
    «… без учета новых субъектов» 2023-2025): для каждого года берём ту,
    где есть значение. МКД = «многоквартирные», если нет — «Жилые здания».
    ИЖС = «построенные населением». Значения тыс. м² → млн (÷1000).
    """
    try:
        df = pd.read_excel(path, sheet_name="Данные", header=None)
    except Exception:  # noqa: BLE001
        return {}
    # строка с годами
    yrow = None
    for i in range(min(6, len(df))):
        yrs = sum(1 for j in range(df.shape[1])
                  if _extract_year(df.iat[i, j]) is not None)
        if yrs >= 2:
            yrow = i
            break
    if yrow is None:
        return {}
    year_cols = {}
    for j in range(df.shape[1]):
        y = _extract_year(df.iat[yrow, j])
        if y is not None:
            year_cols[y] = j

    # собираем по (категория) → {year: value} для РФ-строк (обе вариации)
    cat = {"многокв": {}, "здания": {}, "ижс": {}}
    for i in range(yrow + 1, len(df)):
        region = str(df.iat[i, 0]).lower()
        if "российская федерация" not in region:
            continue
        label = str(df.iat[i, 1]).lower()
        if "многоквартирн" in label:
            key = "многокв"
        elif "построенные населением" in label or "построенных населением" in label:
            key = "ижс"
        elif "жилые здания" in label:
            key = "здания"
        else:
            continue
        for y, j in year_cols.items():
            val = _to_float(df.iat[i, j])
            if not pd.isna(val):
                # перекрываем только если ещё нет (первая РФ-строка приоритетна,
                # но варианты не пересекаются по годам, так что неважно)
                cat[key].setdefault(y, val)

    out: dict[int, dict] = {}
    for y in sorted(year_cols):
        mkd = cat["многокв"].get(y)
        if mkd is None:
            mkd = cat["здания"].get(y)
        izhs = cat["ижс"].get(y)
        if mkd is None and izhs is None:
            continue
        out[y] = {
            "МКД": (mkd / 1000.0) if mkd is not None else float("nan"),
            "ИЖС": (izhs / 1000.0) if izhs is not None else float("nan"),
        }
    return out


@st.cache_data(show_spinner=False, ttl=300)
def load_emiss_34118() -> pd.DataFrame:
    """РФ-ввод жилья из ЕМИСС 34118: DataFrame[year, МКД, ИЖС] в млн м².

    Берёт статичную базу (emiss_34118_base.xls) + свежий живой экспорт из
    downloads/ (если есть); живые годы перекрывают базу.
    """
    def raw_sources() -> list[Path]:
        out: list[Path] = []
        base_for_raw = _vvod_dir()
        if base_for_raw is not None:
            out.extend(_raw_files([base_for_raw], ["emiss_34118_base.xls"]))
        downloads_for_raw = PROJECT_ROOT / "downloads"
        if downloads_for_raw.exists():
            out.extend(_raw_files(
                [downloads_for_raw],
                ["*Введено в действие общей площади жилых домов*.xls*"],
            ))
        return out

    mart = _load_realty_mart("emiss_34118", raw_sources)
    if mart is not None:
        return mart

    merged: dict[int, dict] = {}
    base = _vvod_dir()
    if base is not None:
        bp = base / "emiss_34118_base.xls"
        if bp.exists():
            merged.update(_parse_emiss_34118_file(bp))

    downloads = Path(__file__).resolve().parent.parent / "downloads"
    if downloads.exists():
        live = sorted(
            list(downloads.glob("*Введено в действие общей площади жилых домов*.xls*")),
            key=lambda p: p.stat().st_mtime,
        )
        if live:
            for y, v in _parse_emiss_34118_file(live[-1]).items():
                merged[y] = v  # живые перекрывают базу

    if not merged:
        return pd.DataFrame(columns=["year", "МКД", "ИЖС"])
    rows = sorted(merged)
    return pd.DataFrame({
        "year": rows,
        "МКД": [merged[y].get("МКД", float("nan")) for y in rows],
        "ИЖС": [merged[y].get("ИЖС", float("nan")) for y in rows],
    })


def monitoring_by_year(
    rv: pd.DataFrame,
    *,
    gruppirovka: str | None = None,
    istochnik: list[str] | None = None,
    gk: str | None = None,
    value_col: str = "Общая площадь",
    year_from: int = 2011,
    year_to: int = 2026,
) -> pd.DataFrame:
    """Σ выбранной площади (млн м²) Реестра РВ по «Год ввода по Мосстату».

    Фильтры: gruppirovka (Жилье/Нежилье), istochnik (список «Источник
    финансирования»), gk (точное «Группа компаний»). Возвращает
    DataFrame[year, value] за [year_from, year_to].
    """
    if rv is None or rv.empty:
        return pd.DataFrame(columns=["year", "value"])
    df = rv
    if gruppirovka is not None and "Группировка" in df.columns:
        df = df[df["Группировка"] == gruppirovka]
    if istochnik is not None and "Источник финансирования" in df.columns:
        df = df[df["Источник финансирования"].isin(istochnik)]
    if gk is not None and "Группа компаний" in df.columns:
        df = df[df["Группа компаний"].astype(str).str.strip() == gk]
    if df.empty or "Год ввода по Мосстату" not in df.columns:
        return pd.DataFrame(columns=["year", "value"])
    if value_col not in df.columns:
        return pd.DataFrame(columns=["year", "value"])
    y = pd.to_numeric(df["Год ввода по Мосстату"], errors="coerce")
    area = pd.to_numeric(df[value_col], errors="coerce").fillna(0.0)
    g = (
        pd.DataFrame({"year": y, "area": area})
        .dropna(subset=["year"])
        .assign(year=lambda d: d["year"].astype(int))
        .query("@year_from <= year <= @year_to")
        .groupby("year", as_index=False)["area"].sum()
    )
    g["value"] = g["area"] / 1e6  # м² → млн м²
    return g[["year", "value"]]


@st.cache_data(show_spinner=False, ttl=300)
def load_monitoring_2011_2026_static() -> dict[str, pd.DataFrame]:
    """Static Moscow budget/non-budget rows from monitoring sheet `2011-2026`.

    Values in the workbook are stored in thousand m²; dashboard charts use mln m².
    """
    empty = {
        "residential_budget_split": pd.DataFrame(columns=["year", "Бюджет", "Небюджет", "Итого"]),
        "nonres_budget_split": pd.DataFrame(columns=["year", "Бюджет", "Небюджет", "Итого"]),
    }
    files = _raw_files(MONITORING_PATHS, ["monitoring_2_0_*.xlsx"])
    if not files:
        return empty
    latest = max(files, key=lambda p: p.stat().st_mtime)
    try:
        xl = pd.ExcelFile(latest)
    except Exception:  # noqa: BLE001
        return empty

    sheet = next((s for s in xl.sheet_names if str(s).strip() == "2011-2026"), None)
    if sheet is None:
        return empty

    try:
        df = pd.read_excel(latest, sheet_name=sheet, header=None)
    except Exception:  # noqa: BLE001
        return empty

    def norm(value: object) -> str:
        if value is None or pd.isna(value):
            return ""
        return " ".join(str(value).replace("\n", " ").casefold().split())

    def strict_year(value: object) -> int | None:
        if value is None or pd.isna(value):
            return None
        if isinstance(value, (int, float)) and not pd.isna(value):
            year = int(value)
            return year if 2011 <= year <= 2026 and float(year) == float(value) else None
        text = str(value).strip()
        return int(text) if text.isdigit() and 2011 <= int(text) <= 2026 else None

    year_row = None
    year_cols: dict[int, int] = {}
    for i in range(min(12, len(df))):
        found: dict[int, int] = {}
        for j in range(df.shape[1]):
            year = strict_year(df.iat[i, j])
            if year is not None and 2011 <= year <= 2026:
                found[year] = j
        if len(found) >= 10:
            year_row = i
            year_cols = found
            break
    if year_row is None or not year_cols:
        return empty

    section = ""
    residential_row = None
    nonres_row = None
    for i in range(year_row + 1, len(df)):
        label = norm(df.iat[i, 1]) if df.shape[1] > 1 else ""
        metric = norm(df.iat[i, 2]) if df.shape[1] > 2 else ""
        if label:
            section = label
        if "нежилые объекты" in section and "общая пл" in metric:
            nonres_row = i
        elif "жилые объекты" in section and "жилая пл" in metric:
            residential_row = i
        if residential_row is not None and nonres_row is not None:
            break

    def build(row_index: int | None) -> pd.DataFrame:
        if row_index is None:
            return pd.DataFrame(columns=["year", "Бюджет", "Небюджет", "Итого"])
        rows = []
        for year, col in sorted(year_cols.items()):
            total = _to_float(df.iat[row_index, col]) / 1000.0
            budget = _to_float(df.iat[row_index, col + 1]) / 1000.0 if col + 1 < df.shape[1] else float("nan")
            nonbudget = _to_float(df.iat[row_index, col + 2]) / 1000.0 if col + 2 < df.shape[1] else float("nan")
            rows.append({
                "year": year,
                "Бюджет": budget,
                "Небюджет": nonbudget,
                "Итого": total,
            })
        return pd.DataFrame(rows)

    return {
        "residential_budget_split": build(residential_row),
        "nonres_budget_split": build(nonres_row),
    }
