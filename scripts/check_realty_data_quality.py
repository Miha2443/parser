"""Data-quality smoke checks for the realty dashboard marts.

The parser contract checks validate synthetic fixtures. This script validates
the current site-facing mart payloads, so an empty or structurally broken build
does not pass just because Streamlit can import all pages.
"""
from __future__ import annotations

import logging
import contextlib
import io
import os
import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

os.environ["PARSER_REQUIRE_REALTY_MARTS"] = "1"

logging.getLogger("streamlit").setLevel(logging.ERROR)
logging.getLogger("streamlit.runtime.caching.cache_data_api").setLevel(logging.ERROR)
logging.disable(logging.CRITICAL)

from app.data_access import (  # noqa: E402
    load_erzrf_cards,
    load_erzrf_top,
    load_kvartirografia,
    load_monitoring_2_0,
    load_rasprodannost,
    monitoring_by_year,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def _require_columns(frame: pd.DataFrame, columns: set[str], label: str) -> None:
    missing = columns - set(map(str, frame.columns))
    _require(not missing, f"{label} missing columns: {sorted(missing)}")


def _require_regions(regions: list[str], label: str) -> None:
    _require({"rf", "msk"}.issubset(set(regions)), f"{label} should include rf and msk")


def _name_col(frame: pd.DataFrame) -> str | None:
    return next((c for c in frame.columns if "Наименование" in str(c)), None)


def _place_col(frame: pd.DataFrame) -> str | None:
    return next((c for c in frame.columns if str(c).strip() == "Место"), None)


def _pik_place(frame: pd.DataFrame) -> int | None:
    name_col = _name_col(frame)
    place_col = _place_col(frame)
    if not name_col or not place_col:
        return None
    names = frame[name_col].astype(str).str.strip()
    rows = frame[names.str.startswith("ПИК", na=False)]
    if rows.empty:
        return None
    place = pd.to_numeric(rows[place_col], errors="coerce").dropna()
    return int(place.iloc[0]) if not place.empty else None


def _kvart_name_looks_like_region(name: object) -> bool:
    s = f" {str(name or '').strip().casefold()}"
    markers = (
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
    return any(marker in s for marker in markers)


def check_monitoring() -> None:
    data = load_monitoring_2_0()
    rv = data["rv"]
    oks = data["oks"]
    _require(len(rv) >= 1_000, f"monitoring rv unexpectedly small: {len(rv)}")
    _require(len(oks) >= 1_000, f"monitoring oks unexpectedly small: {len(oks)}")
    _require(len(data["developers"]) >= 100, "monitoring developers unexpectedly small")
    _require_columns(rv, {"Группа компаний", "Общая площадь", "Жилая площадь"}, "monitoring rv")
    _require_columns(oks, {"Группа компаний", "Общая площадь"}, "monitoring oks")
    _require((data["max_year"] or 0) >= 2024, "monitoring max_year is too old")
    zh26 = monitoring_by_year(rv, gruppirovka="Жилье", value_col="Жилая площадь", year_from=2026, year_to=2026)
    _require(not zh26.empty and float(zh26["value"].sum()) > 0, "monitoring residential living area for 2026 is empty")


def check_kvartirografia() -> None:
    data = load_kvartirografia()
    _require_regions(data["regions_available"], "kvartirografia")
    _require(len(data["developers"]) >= 100, "kvartirografia developers unexpectedly small")
    _require(len(data["regions"]) >= 50, "kvartirografia regions unexpectedly small")
    _require_columns(
        data["developers"],
        {"region_key", "наименование", "площадь_тыс_м²_num"},
        "kvartirografia developers",
    )
    msk_devs = data["developers"][data["developers"]["region_key"].astype(str) == "msk"]
    names = msk_devs["наименование"].dropna().astype(str).head(12).tolist()
    region_like = sum(1 for name in names if _kvart_name_looks_like_region(name))
    _require(region_like < max(3, len(names) // 2), "kvartirografia msk developers look like regions")
    _require(str(data.get("report_date") or "").count(".") == 2, "kvartirografia report_date is invalid")


def check_rasprodannost() -> None:
    data = load_rasprodannost()
    _require_regions(data["regions_available"], "rasprodannost")
    _require(len(data["kpi"]) >= 100, "rasprodannost kpi unexpectedly small")
    _require(len(data["developers"]) >= 1_000, "rasprodannost developers unexpectedly small")
    _require(len(data["periods"]) >= 60, "rasprodannost history unexpectedly short")
    latest = data["latest_period"]
    _require(isinstance(latest, tuple) and len(latest) == 2, "rasprodannost latest_period is invalid")
    _require(latest[0] >= 2025, f"rasprodannost latest year is too old: {latest}")
    _require_columns(data["kpi"], {"region_key", "year", "month", "название", "значение_num"}, "rasprodannost kpi")


def check_erzrf() -> None:
    top = load_erzrf_top()
    _require(len(top.get("all_developers", [])) >= 100, "erzrf all_developers unexpectedly small")
    for sorting in ["obyem_stroitelstva", "obyem_vvoda", "nakopl_vvod", "potreb_kachestva", "skorost"]:
        for region in ["rf", "msk"]:
            frame = top.get(sorting, {}).get(region)
            _require(isinstance(frame, pd.DataFrame) and not frame.empty, f"erzrf {sorting}/{region} is empty")
    _require(_pik_place(top["nakopl_vvod"]["rf"]) == 1, "erzrf nakopl_vvod/rf should have PIK at place 1")
    _require(_pik_place(top["nakopl_vvod"]["msk"]) == 1, "erzrf nakopl_vvod/msk should have PIK at place 1")
    cards = load_erzrf_cards()
    _require(len(cards) >= 50, f"erzrf cards unexpectedly small: {len(cards)}")
    _require_columns(cards, {"name_card", "slug", "url"}, "erzrf cards")
    _require(cards["slug"].astype(str).str.len().gt(0).all(), "erzrf cards has empty slug")


def main() -> int:
    check_monitoring()
    check_kvartirografia()
    check_rasprodannost()
    check_erzrf()
    print("realty data quality checks: ok")
    return 0


if __name__ == "__main__":
    with contextlib.redirect_stderr(io.StringIO()):
        raise SystemExit(main())
