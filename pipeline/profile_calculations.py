"""Shared developer-profile calculations; no UI or file writes."""
from __future__ import annotations

import math
from datetime import date, datetime

import pandas as pd
from pipeline.dev_name_utils import normalize_developer_name as norm

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


