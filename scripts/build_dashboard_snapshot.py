"""Freeze existing developer-profile calculations without importing Streamlit.

Only the requested JSON is written. DataAccess retains its mart/raw selection
rules; file hashes and read evidence make that selection independently auditable.
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import importlib.metadata
import json
import math
import sys
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from pipeline.data_access import DataAccess, DataContext  # noqa: E402
from pipeline.dev_name_utils import normalize_developer_name as norm  # noqa: E402

PAGE = Path("app/pages/7_Профиль_застройщика.py")
OUTPUT = Path("frontend/public/profile-snapshot.json")
FAMILIES = {
    "monitoring_2_0": ("monitoring_2_0_*.xlsx",),
    "erzrf_top": ("top_obyem_stroitelstva_rf_*.xlsx",),
    "erzrf_cards": ("cards_*.xlsx",),
    "rasprodannost": ("rasprodannost_*.xlsx",),
    "kvartirografia": ("kvartirografia_*.xlsx", "kvartirografia_*.json"),
    "escrow_manual": ("Наполняемость*.xlsx", "наполняемость*.xlsx", "*эскроу*.xlsx"),
}
CATEGORIES = [
    ("residential", "жилое", "Жилое"),
    ("common", "моп", "МОП"),
    ("nonresidentialInHousing", "нежилое_в_жилом", "Нежилье в жилье"),
    ("standaloneNonresidential", "нежилое_отдельное", "Нежилое отдельное"),
]
CAT_COLS = [f"category_{key}" for _, key, _ in CATEGORIES]
ROOMS = [("1комн", "1 комн"), ("2комн", "2 комн"),
         ("3комн", "3 комн"), ("4+комн", "4+ комн")]
DASH = "—"


def clean(value):
    """Preserve types while converting pandas/numpy missing and infinities."""
    if isinstance(value, dict):
        return {str(k): clean(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [clean(v) for v in value]
    if value is None or value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, (datetime, date, pd.Timestamp)):
        return value.isoformat()
    if hasattr(value, "item"):
        value = value.item()
    if isinstance(value, float) and not math.isfinite(value):
        return None
    return value


def number(value):
    parsed = pd.to_numeric(value, errors="coerce")
    return None if pd.isna(parsed) or not math.isfinite(float(parsed)) else float(parsed)


def page_float(value):
    # Deliberately retains the page's `float(value or 0)`, including NaN.
    return float(value or 0)


def ru_num(value, digits=0):
    if number(value) is None:
        return DASH
    return f"{float(value):,.{digits}f}".replace(",", " ").replace(".", ",")


def percent(num, base):
    return num / base * 100 if num is not None and base is not None and base > 0 else None


def display_percent(value, digits=1):
    return f"{value:.{digits}f}%" if number(value) is not None else DASH


def rows_for(frame, column, key):
    if frame is None or frame.empty or column not in frame.columns:
        return pd.DataFrame()
    mask = frame[column].dropna().apply(lambda value: norm(str(value)) == key)
    return frame[frame[column].notna() & mask]


def records(frame):
    return clean(frame.to_dict("records"))


def categories(frame):
    return {out: float(frame[f"category_{key}"].sum()) if not frame.empty else 0.0
            for out, key, _ in CATEGORIES}


def category_donut(identifier, title, frame, years, construction=False):
    values = categories(frame)
    specs = CATEGORIES if not construction else [
        CATEGORIES[0], CATEGORIES[1],
        ("standaloneNonresidential", "нежилое_отдельное", "Нежилое"),
    ]
    total = sum(values[out] for out, _, _ in specs)
    return {
        "id": identifier, "title": title, "region": "msk", "years": years,
        "status": "available" if total > 0 else "noData", "rowCount": len(frame),
        "segments": [{"key": out, "label": label,
                      "valueM2": values[out] if not frame.empty else None,
                      "displayThousandM2": ru_num(values[out] / 1000)}
                     for out, _, label in specs],
        "totalM2": total if not frame.empty else None,
        "displayTotalThousandM2": ru_num(total / 1000) if total > 0 else DASH,
        "sourceNote": "monitoring_2_0: categorized ОКС, active permit AND under construction; "
                      "three displayed categories, no subtype/coordinate/map filter"
                      if construction else
                      "monitoring_2_0: all normalized-company РВ rows (including historical sheets); "
                      "year equality only for annual donuts; no budget/housing/map restriction. "
                      "Empty selection is null; page sums it to zero and shows no-data notice.",
    }


def annual_data(rv):
    all_years = []
    if not rv.empty:
        grouped = rv.groupby("Год ввода по Мосстату")[CAT_COLS].sum().sort_index()
        for year, row in grouped.iterrows():
            values = {out: float(row[f"category_{key}"]) for out, key, _ in CATEGORIES}
            all_years.append({"year": int(year), "valuesM2": values, "totalM2": sum(values.values())})
    by_year = {row["year"]: row for row in all_years}
    chart = [by_year.get(year, {"year": year, "valuesM2": {out: 0.0 for out, _, _ in CATEGORIES},
                                "totalM2": 0.0})
             for year in range(2022, max(by_year) + 1)] if by_year else []
    legend_frame = rv[rv["Год ввода по Мосстату"] >= 2022] if not rv.empty else rv
    values = categories(legend_frame)
    return {
        "region": "msk", "rows": chart, "allYears": all_years,
        "legend": {"valuesM2": values, "totalM2": sum(values.values()),
                   "displayMillionM2": {key: ru_num(value / 1e6, 1) for key, value in values.items()},
                   "displayTotalMillionM2": ru_num(sum(values.values()) / 1e6, 1),
                   "years": sorted(legend_frame["Год ввода по Мосстату"].astype(int).unique().tolist())
                   if not legend_frame.empty else []},
        "sourceNote": "РВ groupby actual commissioning year; chart 2022..developer maximum year, "
                      "missing chart years zero-filled by the page; legend >=2022; chart unit thousand m2.",
    }


def top_row(top, sorting, region, key):
    frame = top.get(sorting, {}).get(region)
    if frame is None or frame.empty:
        return None
    column = next((c for c in frame.columns if "Наименование" in str(c)), None)
    rows = rows_for(frame, column, key)
    return rows.iloc[0] if not rows.empty else None


def top_value(top, sorting, region, key, substring):
    row = top_row(top, sorting, region, key)
    if row is None:
        return None
    column = next((c for c in row.index if substring in str(c) and "м²" in str(c)), None)
    return float(pd.to_numeric(row[column], errors="coerce") or 0) if column else None


def rating_cell(row):
    selected = {}
    if row is not None:
        for key in ["Место", "Место ", "+/-", "Строится, м²", "Введено, м²", "Рейтинг ЕРЗ", "ЖК", "Регионов"]:
            if key in row.index:
                selected[key.strip()] = row[key]
    place = selected.get("Место")
    display = DASH
    if place is not None and not pd.isna(place):
        try:
            display = str(int(place))
        except (ValueError, TypeError):
            display = str(place)
    return {"place": clean(place), "displayPlace": display, "row": clean(selected) or None}


def ratings_data(top, key):
    construction = {reg: rating_cell(top_row(top, "obyem_stroitelstva", reg, key)) for reg in ("rf", "msk")}
    cumulative = {reg: rating_cell(top_row(top, "nakopl_vvod", reg, key)) for reg in ("rf", "msk")}
    score = None
    for cell in [construction["rf"], cumulative["rf"], construction["msk"], cumulative["msk"]]:
        candidate = (cell["row"] or {}).get("Рейтинг ЕРЗ")
        if candidate:
            score = candidate
            break
    return {"rows": [
        {"id": "cumulative", "title": "По накопленному вводу жилья", **cumulative},
        {"id": "construction", "title": "По объёму текущего строительства", **construction},
    ], "score": score, "displayScore": str(score) if score else None,
        "quality": top.get("nakopl_vvod_quality"),
        "sourceNote": "First normalized-name match. Cumulative=nakopl_vvod, NOT annual obyem_vvoda. "
                      "Score precedence construction RF, cumulative RF, construction Moscow, cumulative Moscow."}


def cards_row(cards, key):
    rows = rows_for(cards, "name_card", key)
    if rows.empty:
        rows = rows_for(cards, "name_table", key)
    return rows.iloc[0] if not rows.empty else None


def housing_values(rv, card, years):
    if card is None or rv.empty or not years:
        return None
    moscow = russia = 0.0
    for year in years:
        rf = pd.to_numeric(card.get(f"Сдано_{year}_м²_num"), errors="coerce")
        rows = rv[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").eq(year)]
        if pd.isna(rf) or rf <= 0 or rows.empty:
            return None
        msk = pd.to_numeric(rows["category_жилое"], errors="coerce").fillna(0).sum()
        if msk < 0 or msk > rf:
            return None
        moscow += float(msk)
        russia += float(rf)
    return {"Москва": moscow, "Другие регионы РФ": russia - moscow}


def housing_data(rv, card, latest):
    years = sorted(rv["Год ввода по Мосстату"].dropna().astype(int).unique().tolist()) if not rv.empty else []
    valid = [y for y in years if y >= 2016 and housing_values(rv, card, [y]) is not None]
    previous = latest - 1 if latest else None
    specs = [("all", valid, f"Ввод за {valid[0]}–{valid[-1]} гг." if valid else "Ввод с 2016 г."),
             ("previous", [previous] if previous else [], f"Ввод за {previous} г."),
             ("latest", [latest] if latest else [], f"Ввод за {latest} г.")]
    result = []
    for identifier, selected, title in specs:
        values = housing_values(rv, card, selected)
        result.append({"id": identifier, "title": title, "years": selected,
                       "status": "available" if values and sum(values.values()) > 0 else "noData",
                       "segments": [{"key": key, "label": label, "valueM2": value,
                                     "displayThousandM2": ru_num(value / 1000)}
                                    for key, (label, value) in zip(("msk", "otherRf"), values.items())]
                       if values else None,
                       "totalM2": sum(values.values()) if values else None,
                       "displayTotalThousandM2": ru_num(sum(values.values()) / 1000) if values else DASH,
                       "sourceNote": "Same calendar years: cards Сдано RF minus РВ category_жилое Moscow. "
                                     "Requires positive RF, existing Moscow rows, 0 <= Moscow <= RF each year."})
    return result


def rasprod_data(payload, key):
    all_rows = rows_for(payload.get("developers"), "наименование", key)
    predicates = {
        "soldPercent": lambda c: "Распроданность" in c and "Отношение" not in c,
        "readinessPercent": lambda c: "Стройготовность" in c and "Отношение" not in c,
        "ratioPercent": lambda c: c.startswith("Отношение распроданности"),
    }
    result = {"sourceNote": "developers sheet; latest year/month independently per normalized company and region. "
                            "Ratio is read from the source, never recomputed from rounded percentages."}
    for region in ("msk", "rf"):
        rows = all_rows[all_rows["region_key"] == region] if not all_rows.empty else all_rows
        row = rows.sort_values(["year", "month"], ascending=False).iloc[0] if not rows.empty else None
        values = {}
        columns = {}
        for name, predicate in predicates.items():
            column = next((c for c in row.index if c.endswith("_num") and predicate(c)), None) if row is not None else None
            columns[name] = column
            values[name] = number(row.get(column)) if column else None
        result[region] = {"period": {"year": int(row["year"]), "month": int(row["month"])} if row is not None else None,
                          "values": values, "display": {name: display_percent(value, 0) for name, value in values.items()},
                          "columns": columns, "row": clean(row.to_dict()) if row is not None else None,
                          "status": "available" if row is not None else "noData"}
    return result


def apartment_regions(payload, key):
    rows = rows_for(payload.get("developers"), "наименование", key)
    return sorted(rows["region_key"].unique().tolist(), key=lambda x: 0 if x == "msk" else 1) if not rows.empty else []


def apartment_data(payload, key, region):
    full = payload.get("developers", pd.DataFrame())
    rows = rows_for(full, "наименование", key)
    available = apartment_regions(payload, key)
    selected = rows[rows["region_key"] == region] if not rows.empty else rows
    if selected.empty:
        return {"region": region, "availableRegions": available, "status": "noData", "rows": None,
                "totalCount": None, "totalAreaThousandM2": None, "averageAreaM2": None, "marketSharePercent": None,
                "sourceNote": "No normalized company row for this region in квартирография developers."}
    row = selected.iloc[0]
    total = page_float(row.get("квартиры_тыс_шт_num")) * 1000
    area = page_float(row.get("площадь_тыс_м²_num"))
    per_dev = rows_for(payload.get("apartments_per_dev"), "наименование", key)
    if per_dev.empty:
        per_dev = rows_for(payload.get("apartments_per_dev"), "monitoring_name", key)
    pd_row = None
    pd_total = None
    accepted = False
    if not per_dev.empty and "region_key" in per_dev.columns:
        pd_region = per_dev[per_dev["region_key"] == region]
        if not pd_region.empty:
            pd_row = pd_region.iloc[0]
            pd_total = page_float(pd_row.get("Все_количество_шт_num"))
            accepted = total > 0 and abs(pd_total / total - 1) < 0.05
    room_rows = []
    for room, label in ROOMS:
        if accepted:
            count = page_float(pd_row.get(f"{room}_количество_шт_num"))
            room_area = page_float(pd_row.get(f"{room}_площадь_тыс_м²_num"))
            share = count / pd_total * 100 if pd_total > 0 else 0
        else:
            share = row.get(f"доля_{room}_%_num")
            count = total * float(share) / 100 if share is not None and not pd.isna(share) else 0
            share = float(share) if share is not None and not pd.isna(share) else 0
            room_area = None
        room_rows.append({"type": label, "count": count if count else None,
                          "sharePercent": share if share else None,
                          "areaThousandM2": room_area if room_area else None,
                          "displayCount": f"{count:.0f}" if number(count) is not None and count else DASH,
                          "displaySharePercent": display_percent(share, 1) if share else DASH,
                          "displayAreaThousandM2": f"{room_area:.1f}" if number(room_area) is not None and room_area else DASH})
    if accepted:
        total = pd_total
        area = page_float(pd_row.get("Все_площадь_тыс_м²_num") or area)
    market_total = float(full[full["region_key"] == region]["площадь_тыс_м²_num"].fillna(0).sum())
    average = area * 1000 / total if total > 0 and area > 0 else None
    share = area / market_total * 100 if market_total > 0 else None
    return {"region": region, "availableRegions": available, "status": "available",
            "totalCount": total, "totalAreaThousandM2": area, "averageAreaM2": average,
            "marketTotalAreaThousandM2": market_total, "marketSharePercent": share,
            "rows": room_rows, "display": {"totalCount": f"{ru_num(total)} шт",
                "totalAreaThousandM2": f"{ru_num(area)} тыс. м²", "averageAreaM2": f"{ru_num(average, 1)} м²" if average is not None else DASH,
                "marketSharePercent": display_percent(share, 2)},
            "source": "apartments_per_dev" if accepted else "developers",
            "aggregateRow": clean(row.to_dict()), "perDevRow": clean(pd_row.to_dict()) if pd_row is not None else None,
            "perDevAccepted": bool(accepted), "reportDate": payload.get("report_date") or None,
            "sourceNote": "First normalized-company regional row. Accept per-dev iff aggregate total >0 and "
                          "abs(per-dev count/aggregate count - 1)<0.05 (strict); otherwise room counts=aggregate "
                          "count*source share/100, areas unavailable. Display table zeroes as null; no 'Все' row. "
                          "Market denominator sums ALL regional developers, including repeated normalized names."}


def delay_data(top, card, rv, oks, key):
    inputs = {}
    for scope in ("rf", "msk"):
        inputs[scope] = {
            "constructionM2": top_value(top, "obyem_stroitelstva", scope, key, "Строится"),
            "constructionDelayM2": top_value(top, "obyem_stroitelstva", scope, key, "С переносом срока"),
            "currentInputM2": top_value(top, "obyem_vvoda", scope, key, "Введено"),
            "currentInputDelayM2": top_value(top, "obyem_vvoda", scope, key, "С переносом срока"),
        }
    historical = rv[rv["Год ввода по Мосстату"].isin([2022, 2023, 2024, 2025])] if not rv.empty else rv
    current = rv[rv["Год ввода по Мосстату"] == 2026] if not rv.empty else rv
    mon_construction = float(oks["Общая площадь"].sum()) if not oks.empty else 0.0
    mon_historical = float(historical["Общая площадь"].sum()) if not historical.empty else 0.0
    mon_current = float(current["Общая площадь"].sum()) if not current.empty else 0.0
    msk_housing = float(historical["category_жилое"].sum()) if not historical.empty else 0.0
    cards_input = sum(page_float(card.get(f"Сдано_{y}_м²_num")) for y in range(2022, 2026)) if card is not None else 0.0
    cards_delay = sum(page_float(card.get(f"Перенос_{y}_м²_num")) for y in range(2022, 2026)) if card is not None else 0.0
    exact_years, paired_years, evidence = [], [], []
    msk_exact_delay = msk_exact_input = 0.0
    paired_rf_delay = paired_msk_delay = paired_rf_input = paired_msk_input = 0.0
    annual = top.get("obyem_vvoda_by_year", {})
    for year in range(2022, 2026):
        selected = {}
        for region in ("msk", "rf"):
            frame = annual.get(region, {}).get(year)
            if frame is not None and not frame.empty:
                name_col = next((c for c in frame.columns if "Наименование" in str(c)), None)
                rows = rows_for(frame, name_col, key)
                if not rows.empty:
                    row = rows.iloc[0]
                    p_col = next((c for c in frame.columns if "С переносом срока" in c and "м²" in c), None)
                    v_col = next((c for c in frame.columns if "Введено" in c and "м²" in c), None)
                    selected[region] = {"inputM2": float(pd.to_numeric(row[v_col], errors="coerce") or 0) if v_col else 0,
                                        "delayM2": float(pd.to_numeric(row[p_col], errors="coerce") or 0) if p_col else 0,
                                        "hasBothColumns": bool(p_col and v_col), "row": clean(row.to_dict())}
        evidence.append({"year": year, "msk": selected.get("msk"), "rf": selected.get("rf")})
        if "msk" not in selected:
            continue
        exact_years.append(year)
        msk_exact_delay += selected["msk"]["delayM2"]
        msk_exact_input += selected["msk"]["inputM2"]
        if "rf" in selected and selected["rf"]["hasBothColumns"]:
            paired_years.append(year)
            paired_rf_delay += selected["rf"]["delayM2"]
            paired_rf_input += selected["rf"]["inputM2"]
            paired_msk_delay += selected["msk"]["delayM2"]
            paired_msk_input += selected["msk"]["inputM2"]
    estimate = cards_delay * min(msk_housing / cards_input, 1.0) if not exact_years and cards_input > 0 and cards_delay > 0 else None
    historical_delay = msk_exact_delay if exact_years else estimate
    rf, msk = inputs["rf"], inputs["msk"]
    def difference(field):
        return max((rf[field] or 0) - (msk[field] or 0), 0.0)
    specs = [
        ("constructionMoscow", "msk", "Перенос в текущем строительстве в Москве", msk["constructionDelayM2"], mon_construction,
         "в стройке Москвы", "ERZ construction Moscow numerator / active ОКС Общая площадь denominator"),
        ("constructionOtherRf", "otherRf", "Перенос в текущем строительстве в регионах РФ", difference("constructionDelayM2"), difference("constructionM2"),
         "в стройке регионов РФ", "max(ERZ RF - ERZ Moscow,0), numerator AND denominator; missing operands page-default to zero"),
        ("historicalMoscow", "msk", f"Перенос ввода в Москве за {min(exact_years)}-{max(exact_years)}" if exact_years else "Перенос ввода в Москве за 2022-2025 (оценка)",
         historical_delay, mon_historical, "введённых в Москве 22-25",
         "Annual ERZ Moscow exact years where available; else cards RF delay * min(РВ Moscow housing/cards RF input,1). "
         "Displayed denominator ALWAYS РВ Общая площадь ALL 2022-2025, even when exact years incomplete."),
        ("historicalOtherRf", "otherRf", "Перенос ввода в регионах РФ за 2022-2025", max(paired_rf_delay - paired_msk_delay, 0.0), max(paired_rf_input - paired_msk_input, 0.0),
         "введённых в регионах РФ 22-25", "Only paired annual ERZ company years, RF requires both input and delay columns; clamped RF-Moscow. "
         "No paired observations means page computes zero; this is NOT confirmed zero delays."),
        ("currentMoscow", "msk", "Перенос ввода в Москве за 2026", msk["currentInputDelayM2"], mon_current,
         "ввода в Москве за 2026", "Current obyem_vvoda Moscow numerator / РВ Общая площадь 2026. Page label is hardcoded 2026; see currentYearByRegion."),
        ("currentOtherRf", "otherRf", "Перенос ввода в регионах РФ за 2026", difference("currentInputDelayM2"), difference("currentInputM2"),
         "ввода в регионах РФ за 2026", "Current ERZ RF-Moscow numerator AND denominator; clamp at zero, page missing operands default zero."),
    ]
    result = []
    for identifier, region, title, value, base, label, note in specs:
        pct = percent(value, base)
        result.append({"id": identifier, "region": region, "title": title,
                       "valueM2": value, "baseM2": base, "percent": pct,
                       "displayValue": f"{ru_num(value / 1000, 1)} тыс. м²" if number(value) is not None and value > 0 else DASH,
                       "displayBase": f"от {ru_num(base / 1000)} тыс. м² {label}",
                       "displayPercent": display_percent(pct), "sourceNote": note})
    card_years = sorted({int(c.split("_")[1]) for c in card.index
                         if str(c).startswith(("Сдано_", "Перенос_", "Уточн_")) and str(c).endswith("_м²_num")}) if card is not None else []
    return {"cards": result, "inputs": {**inputs,
        "monitoringConstructionM2": mon_construction, "monitoringHistoricalInputM2": mon_historical,
        "monitoring2026InputM2": mon_current, "monitoringHistoricalHousingM2": msk_housing,
        "cardsHistoricalRfInputM2": cards_input if card is not None else None,
        "cardsHistoricalRfDelayM2": cards_delay if card is not None else None,
        "exactMoscowYears": exact_years, "pairedYears": paired_years,
        "exactMoscowDelayM2": msk_exact_delay if exact_years else None,
        "exactMoscowInputM2": msk_exact_input if exact_years else None,
        "estimatedMoscowDelayM2": estimate,
        "pairedRfDelayM2": paired_rf_delay, "pairedMoscowDelayM2": paired_msk_delay,
        "pairedRfInputM2": paired_rf_input, "pairedMoscowInputM2": paired_msk_input,
        "currentYearByRegion": top.get("obyem_vvoda_current_year")},
        "annualTopRows": evidence,
        "annualCardRows": [{"year": year, "commissionedM2": card.get(f"Сдано_{year}_м²_num"),
                            "delayedM2": card.get(f"Перенос_{year}_м²_num"),
                            "clarifiedM2": card.get(f"Уточн_{year}_м²_num")} for year in card_years],
        "cardRow": clean(card.to_dict()) if card is not None else None,
        "sourceNote": "Six actual displayed KPI cards, not the obsolete docstring's stacked delay chart. "
                      "annualCardRows are supporting source inputs; card loader converts blank/unparseable cells to zero."}


def escrow_data(frame, key):
    gk_col = next((c for c in frame.columns if "ГК застройщика" in str(c)), None)
    rows = rows_for(frame, gk_col, key)
    if rows.empty:
        return {"region": "msk", "status": "noData", "loanRub": None, "debtRub": None, "revenueRub": None,
                "debtSharePercent": None, "coveragePercent": None, "rowCount": 0,
                "sourceNote": "No company match in escrow register, or missing source/group column; Moscow only."}
    def find_column(*substrings):
        return next((c for c in frame.columns if all(s in str(c) for s in substrings)), None)
    credit = find_column("Сумма кредита")
    debt = find_column("Сумма задолженности")
    revenue = find_column("Выручка от реализации всех площадей") or find_column("Выручка от реализации")
    revenue = next((c for c in frame.columns if "Выручка от реализации всех площадей" in str(c) and "эскроу" not in str(c)), revenue)
    columns = {"loanRub": credit, "debtRub": debt, "revenueRub": revenue}
    values = {out: float(pd.to_numeric(rows[column], errors="coerce").sum()) if column else None
              for out, column in columns.items()}
    share = percent(values["debtRub"], values["loanRub"])
    coverage = percent(values["revenueRub"], values["debtRub"])
    def rub_display(value):
        return f"{value / 1e9:.1f}".replace(".", ",") + " млрд ₽" if number(value) is not None and value != 0 else DASH
    return {"region": "msk", "status": "available", **values,
            "debtSharePercent": share, "coveragePercent": coverage, "rowCount": len(rows), "columns": columns,
            "display": {**{name: rub_display(value) for name, value in values.items()},
                        "debtSharePercent": display_percent(share, 0) if share else DASH,
                        "coveragePercent": display_percent(coverage, 0) if coverage else DASH},
            "sourceNote": "Sum ALL normalized-company rows, no object/status/map restriction. "
                          "Debt share=debt/credit; coverage=ALL-area sales revenue/debt, NOT revenue/credit. "
                          "Missing columns=null (page falls back to zero/dash); numeric sum skips missing cells."}


def object_tables(rv, oks):
    rv_cols = ["Коммерческое наименование", "Наименование объекта", "Округ", "Район", "Отрасли", "Группировка", "Подтип объекта",
               "Общая площадь", "Жилая площадь", "Количество квартир", "Год ввода по Мосстату", "Месяц ввода по Мосстату"]
    oks_cols = ["Коммерческое название", "Наименование объекта", "Округ", "Район", "Назначение", "Подтип объекта",
                "Общая площадь", "Жилая площадь", "Количество квартир", "Процент готовности", "Год ввода по графику", "Статус объекта"]
    result = {}
    for name, frame, columns, sort, ascending in [
        ("commissioned", rv, rv_cols, ["Год ввода по Мосстату", "Общая площадь"], [False, False]),
        ("permitted", oks, oks_cols, ["Статус объекта", "Общая площадь"], [True, False]),
    ]:
        columns = [c for c in columns if c in frame.columns]
        selected = frame[columns].sort_values(sort, ascending=ascending) if not frame.empty else pd.DataFrame(columns=columns)
        result[name] = {"columns": columns, "rows": records(selected), "rowCount": len(selected),
                        "sourceNote": "All company rows, no coordinate/map/sampling restriction. Permitted includes introduced/planned objects."}
    return result


def make_profile(developer, region, data):
    key = norm(developer)
    mon = data["monitoring_2_0"]
    rv = rows_for(mon["rv"], "Группа компаний", key)
    oks_all = rows_for(mon["oks"], "Группа компаний", key)
    oks = oks_all
    filter_note = "No recognized status columns; page leaves ОКС unfiltered"
    if not oks.empty:
        if {"Статус РС", "Ввод в эксплуатацию"}.issubset(oks.columns):
            oks = oks[(oks["Статус РС"] == "Действует") & (oks["Ввод в эксплуатацию"] == "В строительстве")]
            filter_note = "Статус РС == Действует AND Ввод в эксплуатацию == В строительстве"
        elif "Статус объекта" in oks.columns:
            oks = oks[oks["Статус объекта"] == "Строящийся"]
            filter_note = "Fallback: Статус объекта == Строящийся"
    latest = mon["max_year"]
    previous = latest - 1 if latest else None
    last_rv = rv[rv["Год ввода по Мосстату"] == latest] if latest and not rv.empty else pd.DataFrame()
    prev_rv = rv[rv["Год ввода по Мосстату"] == previous] if previous and not rv.empty else pd.DataFrame()
    card = cards_row(data["erzrf_cards"], key)
    years = sorted(rv["Год ввода по Мосстату"].astype(int).unique().tolist()) if not rv.empty else []
    return {"id": f"{key}:{region}", "developer": developer, "developerKey": key, "region": region,
            "controls": {"developer": developer, "apartmentRegion": region, "latestMonitoringYear": latest,
                         "previousMonitoringYear": previous, "constructionFilter": filter_note},
            "ratings": ratings_data(data["erzrf_top"], key), "housingComparison": housing_data(rv, card, latest),
            "categoryDonuts": [category_donut("commissionedAll", "Ввод с 2016 г.", rv, years),
                               category_donut("commissionedPrevious", f"Ввод за {previous} г.", prev_rv, [previous]),
                               category_donut("commissionedLatest", f"Ввод за {latest} г.", last_rv, [latest]),
                               category_donut("construction", "В строительстве (Москва)", oks, [], construction=True)],
            "annual": annual_data(rv), "rasprod": rasprod_data(data["rasprodannost"], key),
            "apartments": apartment_data(data["kvartirografia"], key, region),
            "delays": delay_data(data["erzrf_top"], card, rv, oks, key), "escrow": escrow_data(data["escrow_manual"], key),
            "objects": object_tables(rv, oks_all),
            "sourceNotes": ["region applies only to квартирография; all other geography is unchanged from the page.",
                            "No geographic-coordinate, map eligibility, financing, housing-only or subtype restrictions added."]}


def digest(path):
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def relative(path, root):
    try:
        return Path(path).resolve().relative_to(root).as_posix()
    except ValueError:
        return str(path)


def file_evidence(path, root):
    stat = path.stat()
    return {"path": relative(path, root), "sizeBytes": stat.st_size, "mtimeNs": stat.st_mtime_ns,
            "modifiedAtUtc": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(), "sha256": digest(path)}


class ObservedAccess(DataAccess):
    def __init__(self, context):
        super().__init__(context)
        self.mart_reads = []

    def _load_realty_mart(self, name, raw_files):
        value = super()._load_realty_mart(name, raw_files)
        self.mart_reads.append({"family": name, "accepted": value is not None,
                                "path": f"data/marts/realty/{name}.pkl"})
        return value


def verify_page_helpers(root, data, profiles):
    """Compile only pure function definitions from the reference page, never imports/UI."""
    tree = ast.parse((root / PAGE).read_text(encoding="utf-8"))
    wanted = {"categorize_sum", "find_dev_rows", "get_rating", "erzrf_value", "get_cards_row", "housing_comparison"}
    definitions = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in wanted]
    if {node.name for node in definitions} != wanted:
        raise RuntimeError("Reference-page helper contract changed; review snapshot mapping")
    env = {"pd": pd, "norm": norm, "CAT_LABELS": [label for _, _, label in CATEGORIES],
           "CAT_KEYS": [key for _, key, _ in CATEGORIES], "CAT_COL_PREFIX": "category_",
           "erzrf_top": data["erzrf_top"], "erzrf_cards": data["erzrf_cards"]}
    exec(compile(ast.Module(body=definitions, type_ignores=[]), str(PAGE), "exec"), env)
    checks = 0
    for profile in profiles:
        key = profile["developerKey"]
        env["sel_key"] = key
        rv = env["find_dev_rows"](data["monitoring_2_0"]["rv"], "Группа компаний", key)
        env["rv_dev"] = rv
        env["cards_row"] = env["get_cards_row"]()
        expected = list(env["categorize_sum"](rv).values())
        actual = [segment["valueM2"] for segment in profile["categoryDonuts"][0]["segments"]]
        assert clean(expected) == ([0.0] * 4 if rv.empty else actual), (profile["id"], "category sums")
        checks += 1
        for sorting, row in zip(("nakopl_vvod", "obyem_stroitelstva"), profile["ratings"]["rows"]):
            for region in ("rf", "msk"):
                assert clean(env["get_rating"](sorting, region)) == (row[region]["row"] or {}), (profile["id"], "rating")
                checks += 1
        for item in profile["housingComparison"]:
            expected = env["housing_comparison"](item["years"])
            actual = {seg["label"]: seg["valueM2"] for seg in item["segments"]} if item["segments"] else None
            assert clean(expected) == actual, (profile["id"], "housing comparison")
            checks += 1
        for region in ("rf", "msk"):
            for field, sorting, substring in [
                ("constructionM2", "obyem_stroitelstva", "Строится"),
                ("constructionDelayM2", "obyem_stroitelstva", "С переносом срока"),
                ("currentInputM2", "obyem_vvoda", "Введено"),
                ("currentInputDelayM2", "obyem_vvoda", "С переносом срока"),
            ]:
                assert clean(env["erzrf_value"](sorting, region, substring)) == profile["delays"]["inputs"][region][field]
                checks += 1
    return {"referenceHelperChecks": checks, "referenceHelpers": sorted(wanted),
            "method": "AST-isolated pure functions from current profile page; no Streamlit/facade import",
            "scope": "Company matching, all-input categories, four rating rows, housing comparison, ERZ delay operands. "
                     "Not proof of complete UI, formatting, apartments, escrow or six-card parity."}


def verify_page_output(root, snapshot):
    """Execute the original page with explicit core data and a capturing UI stub.

    Imports are excluded, but all original selection/calculation/render code is
    executed. This checks displayed data, not browser/Streamlit visual behavior.
    """
    import plotly.graph_objects as go

    access = DataAccess(DataContext(root, use_marts=snapshot["provenance"]["context"]["useMarts"],
                                    require_marts=snapshot["provenance"]["context"]["requireMarts"]))
    data = {name: getattr(access, f"load_{name}")() for name in FAMILIES}
    tree = ast.parse((root / PAGE).read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    code = compile(tree, str(PAGE), "exec")

    class Capture:
        def __init__(self, profile):
            self.profile = profile
            self.metrics, self.frames, self.figures, self.html = [], [], [], []
            self.sidebar = self.column_config = self

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def columns(self, widths):
            return [self] * (widths if isinstance(widths, int) else len(widths))

        def selectbox(self, label, options, **kwargs):
            assert self.profile["developer"] in options
            return self.profile["developer"]

        def radio(self, label, options, **kwargs):
            assert self.profile["region"] in options
            return self.profile["region"]

        def metric(self, label, value, *args, **kwargs):
            self.metrics.append((label, value))

        def dataframe(self, frame, **kwargs):
            self.frames.append(frame)

        def plotly_chart(self, figure, **kwargs):
            self.figures.append((kwargs["key"], figure))

        def markdown(self, text, **kwargs):
            self.html.append(text)

        def __getattr__(self, name):
            return lambda *args, **kwargs: self

    for profile in snapshot["profiles"]:
        ui = Capture(profile)
        env = {"pd": pd, "go": go, "st": ui, "norm": norm,
               "COLORS": {key: "#333333" for key in ("green", "amber", "blue", "red", "text", "muted", "stroke", "ink", "line", "panel")},
               "apply_theme": lambda: None, "page_header": lambda *a, **k: None,
               "style_plotly": lambda *a, **k: None, "chart_data_expander": lambda *a, **k: None,
               "latest_realty_mart_source_date": access.latest_realty_mart_source_date,
               "latest_raw_source_date": access.latest_raw_source_date}
        env.update({f"load_{name}": (lambda value=value: value) for name, value in data.items()})
        exec(code, env)
        expected_metrics = []
        expected_frames = []
        apartments = profile["apartments"]
        if apartments["status"] == "available":
            expected_frames.append([{"Тип": row["type"], "Количество, шт": row["count"],
                                     "Доля, %": row["sharePercent"], "Площадь, тыс. м²": row["areaThousandM2"]}
                                    for row in apartments["rows"]])
            expected_metrics += [(title, apartments["display"][field]) for title, field in [
                ("Квартиры", "totalCount"), ("Площадь", "totalAreaThousandM2"),
                ("Ср. площадь квартиры", "averageAreaM2"), ("Доля рынка региона", "marketSharePercent")]]
        for name in ("commissioned", "permitted"):
            table = profile["objects"][name]
            if table["rows"]:
                expected_frames.append(table["rows"])
        assert [clean(frame.to_dict("records")) for frame in ui.frames] == clean(expected_frames), (profile["id"], "tables")
        escrow = profile["escrow"]
        if escrow["status"] == "available":
            expected_metrics += [(title, escrow["display"][field]) for title, field in [
                ("Объём займов", "loanRub"), ("Остаток выплат", "debtRub"), ("% Доля остатка", "debtSharePercent"),
                ("Выручка от продаж", "revenueRub"), ("Покрытие займов выручкой", "coveragePercent")]]
        assert ui.metrics == expected_metrics, (profile["id"], "displayed metrics")
        for prefix, donuts in [("structure", profile["categoryDonuts"]), ("housing", profile["housingComparison"])]:
            for donut in donuts:
                if donut["status"] != "available":
                    continue
                figure = next(fig for key, fig in ui.figures if key == f"donut_{prefix}_{donut['title']}")
                assert list(figure.data[0].values) == [s["valueM2"] for s in donut["segments"]], (profile["id"], donut["id"])
        if profile["annual"]["rows"]:
            bar = next(fig for key, fig in ui.figures if key == "dynamics_bar")
            for trace, category in zip(bar.data[:4], snapshot["schema"]["categoryKeys"]):
                assert list(trace.y) == [row["valuesM2"][category] / 1000 for row in profile["annual"]["rows"]], (profile["id"], "annual")
        delay_variables = [("perenos_stroy_msk", "mon_stroy_msk"), ("regiony_stroy_value", "regiony_stroy_base"),
                           ("perenos_msk_2225", "mon_vvod_msk_2225"), ("regiony_2225_value", "regiony_2225_base"),
                           ("perenos_vvod_msk_2026", "mon_vvod_msk_2026"), ("regiony_2026_value", "regiony_2026_base")]
        for card, (value, base) in zip(profile["delays"]["cards"], delay_variables):
            assert clean(env[value]) == card["valueM2"] and clean(env[base]) == card["baseM2"], (profile["id"], card["id"], "operands")
            html = next(text for text in ui.html if card["title"] in text and "font-size:34px" in text)
            assert all(card[field] in html for field in ("displayValue", "displayBase", "displayPercent")), (profile["id"], card["id"], "display")
        if "find_num" in env:
            for region in ("msk", "rf"):
                original_row, _ = env["latest_region_row"](region)
                for field, (_, predicate) in zip(("soldPercent", "readinessPercent", "ratioPercent"), env["preds"]):
                    actual = env["find_num"](original_row, predicate)
                    expected = profile["rasprod"][region]["display"][field]
                    assert actual == expected, (profile["id"], region, field, actual, expected)
        print(f"Original-page capture passed: {profile['id']} (metrics={len(ui.metrics)}, "
              f"tables={len(ui.frames)}, plots={len(ui.figures)}, delay cards=6)")
    print("Captured numeric/display data only; browser layout, source truth and full parity not certified")


def build_snapshot(root, generated_at, use_marts=True, require_marts=False):
    context = DataContext(root, root / "downloads", use_marts=use_marts, require_marts=require_marts)
    access = ObservedAccess(context)
    family_files = {name: access.source_files(name) for name in FAMILIES}
    input_paths = set(path for files in family_files.values() for path in files)
    input_paths.update(root / "data/marts/realty" / f"{name}.pkl" for name in FAMILIES)
    input_paths.add(root / "data/marts/realty/manifest.json")
    input_paths = {path.resolve() for path in input_paths if path.is_file()}
    before = {path: file_evidence(path, root) for path in sorted(input_paths)}
    versions = {name: access.data_version(f"load_{name}") for name in FAMILIES}
    opened, current_family = {name: set() for name in FAMILIES}, [None]
    def audit(event, args):
        if event == "open" and current_family[0] is not None and isinstance(args[0], (str, bytes)):
            path = Path(args[0]).resolve()
            if path in input_paths:
                opened[current_family[0]].add(path)
    sys.addaudithook(audit)
    data = {}
    try:
        for name in FAMILIES:
            current_family[0] = name
            data[name] = getattr(access, f"load_{name}")()
    finally:
        current_family[0] = None
    source_dates = {name: access.latest_realty_mart_source_date(name) or access.latest_raw_source_date(*patterns) or None
                    for name, patterns in FAMILIES.items()}
    mon = data["monitoring_2_0"]
    if not mon.get("developers"):
        raise RuntimeError("Monitoring has no actual developer selector; no profiles can be frozen")
    requested = [("пик", "ПИК"), ("самолет", "САМОЛЕТ"), ("capital group", "CAPITAL GROUP")]
    profiles, developers, excluded = [], [], []
    for key, preferred in requested:
        names = [name for name in mon["developers"] if norm(name) == key]
        if not names:
            excluded.append({"developerKey": key, "reason": "Absent from actual monitoring selector"})
            continue
        canonical = preferred if preferred in names else min(names, key=lambda name: (len(name), name))
        regions = apartment_regions(data["kvartirografia"], key)
        regions = [reg for reg in regions if reg in ("msk", "rf")]
        # No apartment rows must not remove a genuine monitoring profile.
        regions = regions or ["msk"]
        developers.append({"developer": canonical, "developerKey": key, "monitoringAliases": names, "regions": regions})
        for region in regions:
            profiles.append(clean(make_profile(canonical, region, data)))
    verification = verify_page_helpers(root, data, profiles)
    after = {path: file_evidence(path, root) for path in sorted(input_paths)}
    after_versions = {name: access.data_version(f"load_{name}") for name in FAMILIES}
    if before != after or versions != after_versions:
        raise RuntimeError("Source inventory/content/version changed during extraction; artifact not written")
    def portable_quality(value):
        if isinstance(value, dict):
            return {k: relative(v, root) if k == "source" and isinstance(v, str) else portable_quality(v) for k, v in value.items()}
        if isinstance(value, list):
            return [portable_quality(v) for v in value]
        return value
    source_evidence = {}
    for name in FAMILIES:
        source_evidence[name] = {
            "candidateRawFiles": [relative(path, root) for path in family_files[name]],
            "openedInputs": [relative(path, root) for path in sorted(opened[name])],
            "martReadAttempts": [item for item in access.mart_reads if item["family"] == name],
            "dataVersion": [{"path": relative(item[0], root), "signature": list(item[1:])} for item in versions[name]],
            "dateLabel": source_dates[name],
            "dateBasis": "manifest latest source mtime, otherwise raw matching file mtime; same as page, "
                         "not certified observation/download date or necessarily selected raw file date",
        }
    code_paths = [PAGE, Path("pipeline/data_access.py"), Path("pipeline/dev_name_utils.py"),
                  Path("pipeline/paths.py"), Path("pipeline/source_records.py"), Path("scripts/build_dashboard_snapshot.py")]
    schema = {
        "numericPolicy": "Finite JSON numbers only. Unknown/non-finite -> null; no imputation beyond existing loader/page rules. "
                         "Page-calculated defaults/zeroes documented per section; display fields retain dash semantics.",
        "regionPolicy": "profiles[].region controls apartments ONLY; other sections retain explicit msk/rf/otherRf scopes.",
        "profiles": "Array of real monitoring selector developers x actual apartment scopes; id=normalizedName:region.",
        "ratings": "{rows:[{id,title,rf:{place,displayPlace,row},msk:{place,displayPlace,row}}],score,displayScore,quality,sourceNote}",
        "categoryDonuts": "Four {id,title,region,years,status,rowCount,segments:[{key,label,valueM2,displayThousandM2}],totalM2,displayTotalThousandM2,sourceNote}",
        "housingComparison": "Three {id,title,years,status,segments|null,totalM2,displayTotalThousandM2,sourceNote}; segments scopes msk,otherRf.",
        "annual": "{region,rows:[{year,valuesM2,totalM2}],allYears,legend:{valuesM2,totalM2,displayMillionM2,displayTotalMillionM2,years},sourceNote}",
        "categoryKeys": [out for out, _, _ in CATEGORIES],
        "rasprod": "{msk,rf,sourceNote}; each {period:{year,month}|null,values:{soldPercent,readinessPercent,ratioPercent},display,columns,row,status}",
        "apartments": "{region,availableRegions,status,totalCount,totalAreaThousandM2,averageAreaM2,marketTotalAreaThousandM2,marketSharePercent,rows:[{type,count,sharePercent,areaThousandM2,displayCount,displaySharePercent,displayAreaThousandM2}]|null,display,source,aggregateRow,perDevRow,perDevAccepted,reportDate,sourceNote}",
        "delays": "{cards:[{id,region,title,valueM2,baseM2,percent,displayValue,displayBase,displayPercent,sourceNote}],inputs,annualTopRows,annualCardRows,cardRow,sourceNote}",
        "escrow": "{region:'msk',status,loanRub,debtRub,revenueRub,debtSharePercent,coveragePercent,rowCount,columns,display,sourceNote}; display/columns absent when noData.",
        "objects": "{commissioned,permitted}; each {columns:string[],rows:object[],rowCount,sourceNote}; ALL rows, source column names, no sample.",
        "units": {"*M2": "square metres", "*ThousandM2": "thousand square metres", "*Rub": "rubles", "*Percent": "percent points", "*Count": "apartments (aggregate-derived room counts may be fractional)"},
        "displayPolicy": "Explicit display fields are current-page labels. ru_num uses Python rounding, ordinary spaces, decimal comma. "
                         "Percent labels use decimal dot; apartments table uses pandas/Streamlit NumberColumn formatting.",
    }
    snapshot = portable_quality(clean({
        "schemaVersion": 1, "generatedAt": generated_at, "sourceDates": source_dates, "schema": schema,
        "controls": {"developers": developers, "excludedRequestedDevelopers": excluded,
                     "defaultProfileId": profiles[0]["id"] if profiles else None,
                     "regionControl": "apartmentsOnly", "frozen": True, "latestMonitoringYear": mon.get("max_year"),
                     "sourceMonitoringYears": [mon.get("min_year"), mon.get("max_year")],
                     "developerSelectionNote": "Three requested companies only; canonical actual selector name preferred. "
                                               "All same-normalized-name aliases are matched, just like existing page; no invented companies/scopes."},
        "provenance": {"context": {"root": ".", "downloads": "downloads", "useMarts": use_marts, "requireMarts": require_marts},
                       "inputs": list(before.values()), "sources": source_evidence,
                       "code": [{"path": path.as_posix(), "sha256": digest(root / path)} for path in code_paths],
                       "runtime": {"python": sys.version.split()[0], "pandas": pd.__version__, "openpyxl": importlib.metadata.version("openpyxl")},
                       "issues": access.issues, "unchangedInputsVerified": True,
                       "selectionNote": "DataAccess chooses by mtime and validates/falls back as existing UI. openedInputs includes "
                                        "rejected/validated raw reads and manifest reads, not a claim every opened file contributed values. "
                                        "Candidate registry hashes and native dataVersion preserved, including unused candidates."},
        "profiles": profiles, "verification": verification,
    }))
    snapshot["contentSha256"] = hashlib.sha256(json.dumps(snapshot, ensure_ascii=False, sort_keys=True,
                                                          allow_nan=False, separators=(",", ":")).encode("utf-8")).hexdigest()
    return snapshot


def serialize(snapshot):
    return json.dumps(snapshot, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument("--output", type=Path, default=OUTPUT)
    parser.add_argument("--generated-at", help="ISO-8601 generation timestamp with timezone; pin for byte reproducibility")
    parser.add_argument("--raw", action="store_true", help="Disable marts, keeping existing raw fallback rules")
    parser.add_argument("--require-marts", action="store_true")
    parser.add_argument("--check", action="store_true", help="Rebuild using existing generatedAt and compare full bytes, without writing")
    parser.add_argument("--verify-page", action="store_true", help="Capture and compare original-page values without Streamlit imports (requires Plotly)")
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output.resolve() if args.output.is_absolute() else root / args.output
    generated_at = args.generated_at or datetime.now(timezone.utc).isoformat(timespec="seconds")
    if args.check:
        generated_at = json.loads(output.read_text(encoding="utf-8"))["generatedAt"]
    timestamp = datetime.fromisoformat(generated_at.replace("Z", "+00:00"))
    if timestamp.tzinfo is None:
        parser.error("--generated-at must include a timezone")
    snapshot = build_snapshot(root, generated_at, not args.raw, args.require_marts)
    if args.verify_page:
        verify_page_output(root, snapshot)
    if "streamlit" in sys.modules or "app.data_access" in sys.modules:
        raise RuntimeError("Snapshot extraction unexpectedly imported Streamlit/facade")
    encoded = serialize(snapshot).encode("utf-8")
    if args.check:
        if output.read_bytes() != encoded:
            raise SystemExit("Snapshot differs from local inputs/code/runtime; inspect/rebuild explicitly")
        print("Reproducibility check passed: full artifact bytes match")
    else:
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_bytes(encoded)
        print(f"Wrote {relative(output, root)} ({len(encoded):,} bytes)")
    print(f"Profiles: {len(snapshot['profiles'])}; source files: {len(snapshot['provenance']['inputs'])}; "
          f"reference-helper checks: {snapshot['verification']['referenceHelperChecks']}")
    print(f"Content SHA256: {snapshot['contentSha256']}")


if __name__ == "__main__":
    main()
