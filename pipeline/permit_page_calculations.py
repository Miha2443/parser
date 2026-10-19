"""Supplemental permit series from cached, explicitly identified source cells."""
from __future__ import annotations

from datetime import datetime
import hashlib
import math
import re

import pandas as pd
from openpyxl.utils import get_column_letter

from pipeline.operational_calculations import MONTH_LABELS


HOUSING_SHEET = "Вспомогат1"
HISTORY_SHEET = "Данные с 2011 года"
OKS_SHEET = "Реестр ОКС"
FORECAST_SHEET = "Прогнозы"
FORECAST_FIELDS = ("housing_living", "housing_total", "mop_embedded", "nonresidential",
                   "offices", "industry", "social", "trade", "hotels", "other")
HISTORICAL_FIELDS = (("offices", "Административно-деловые"), ("industry", "Промышленные"),
                     ("trade", "Торговые"), ("social", "Социальные"),
                     ("hotels", "Гостиницы"), ("other", "Прочие"))


def normalized(value):
    return " ".join(str(value or "").split()).casefold().replace("ё", "е")


def number(value):
    if isinstance(value, str):
        value = re.sub(r"\s", "", value).replace(",", ".")
    if isinstance(value, bool) or value is None or value == "":
        return None
    try:
        result = float(value)
    except (TypeError, ValueError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def strict_sum(values):
    values = list(values)
    return sum(values) if values and all(value is not None for value in values) else None


def _cell_number(cell, notes, scale=1):
    value = number(cell.value)
    if value is None and cell.value is not None and str(cell.value).strip():
        notes.append(f"{cell.coordinate}: значение не является конечным числом; показано null.")
    return value / scale if value is not None else None


def read_forecast(book, source_file):
    notes = ["Единица в строках 1–12 источника не указана. Рабочее предположение: тыс. м², "
             "согласованное с масштабом прежнего графика; единица не подтверждена источником.",
             "Годовой прогноз показан отдельно от фактических разрешений, остатка и темпов роста. "
             "Использованы только сохранённые значения ячеек, без пересчёта формул и коэффициентов."]
    result = {"unit": "тыс. м²", "unitBasis": "assumed_thousand_m2",
              "source": {"file": source_file, "sheet": FORECAST_SHEET}, "rows": [], "notes": notes}
    if FORECAST_SHEET not in book.sheetnames:
        notes.append(f"Лист «{FORECAST_SHEET}» отсутствует; прогноз недоступен.")
        return result
    cells = list(book[FORECAST_SHEET].iter_rows(min_row=2, max_row=12, min_col=4, max_col=13))
    seen = set()
    for column in range(10):
        letter = get_column_letter(column + 4)
        year = number(cells[0][column].value)
        if year is None or year != int(year) or not 1900 <= year <= 2200 or year in seen:
            notes.append(f"{letter}2: отсутствующий или неоднозначный год; столбец пропущен.")
            continue
        seen.add(year)
        row = {"year": int(year), "cells": {"year": f"{letter}2"}}
        for offset, field in enumerate(FORECAST_FIELDS, start=1):
            cell = cells[offset][column]
            row[field] = _cell_number(cell, notes)
            row["cells"][field] = f"{letter}{offset + 2}"
        result["rows"].append(row)
    if not result["rows"]:
        notes.append("Нет столбцов прогноза с подтверждённым годом.")
    return result


def select_forecast(forecast, kind):
    # The caller owns this generation; selection never mutates the cached rows.
    return {**forecast, "rows": [{**row, "value": row["housing_living"] if kind == "housing" else
                                  row["nonresidential"] if kind == "nonresidential" else
                                  strict_sum((row["housing_living"], row["nonresidential"]))}
                                 for row in forecast["rows"]]}


def _gap(total, living, notes, source):
    if total is None or living is None:
        return None
    if total < living:
        notes.append(f"{source}: общая площадь меньше жилой; МОП и встроенное нежильё = null, не ноль.")
        return None
    return total - living


def _housing_history(book, notes):
    if HOUSING_SHEET not in book.sheetnames:
        notes.append(f"Лист «{HOUSING_SHEET}» отсутствует; историческая структура жилья недоступна.")
        return {}
    rows = list(book[HOUSING_SHEET].iter_rows(min_row=1, max_row=146, min_col=2, max_col=4))
    if normalized(rows[2][1].value) != "общая" or normalized(rows[2][2].value) != "жилая":
        notes.append("Вспомогат1!C3:D3: заголовки ОБЩАЯ/ЖИЛАЯ не подтверждены; историческая структура недоступна.")
        return {}
    result, year = {}, None
    stems = ("янв", "фев", "мар", "апр", "май", "июн", "июл", "авг", "сен", "окт", "ноя", "дек")
    for label, total_cell, living_cell in rows[3:]:
        numeric_year = number(label.value)
        if numeric_year is not None and numeric_year == int(numeric_year) and 2011 <= numeric_year <= 2021:
            year = int(numeric_year)
            continue
        month = next((index for index, stem in enumerate(stems, 1) if normalized(label.value).startswith(stem)), None)
        if year is None or month is None:
            continue
        key = (year, month)
        if key in result:
            notes.append(f"{HOUSING_SHEET}!{label.coordinate}: повтор месяца; историческая структура недоступна.")
            return {}
        total = _cell_number(total_cell, notes, 1000)
        living = _cell_number(living_cell, notes, 1000)
        source = f"{HOUSING_SHEET}!{total_cell.coordinate}:{living_cell.coordinate}"
        result[key] = {"housing_total": total, "living": living,
                       "mop_embedded": _gap(total, living, notes, source), "source": source}
    return result


def _nonres_history(book, notes):
    if HISTORY_SHEET not in book.sheetnames:
        notes.append(f"Лист «{HISTORY_SHEET}» отсутствует; исторические категории нежилья недоступны.")
        return {}
    rows = list(book[HISTORY_SHEET].iter_rows(min_row=114, max_row=246, min_col=6, max_col=15))
    expected = ("год", "№ месяца", "месяц", "нежилье, всего", "административно- деловые",
                "промышленные", "торговые", "социальные", "гостиницы", "прочие")
    # Whitespace in wrapped labels is presentation, not a different taxonomy.
    clean = lambda value: normalized(value).replace("- ", "-").replace(", ", ",")
    if [clean(cell.value) for cell in rows[0]] != [clean(value) for value in expected]:
        notes.append("Данные с 2011 года!F114:O114: заголовки исторических категорий не подтверждены; разрез недоступен.")
        return {}
    result = {}
    for cells in rows[1:]:
        year, month = number(cells[0].value), number(cells[1].value)
        if year is None or month is None or year != int(year) or month != int(month):
            continue
        if not 2011 <= year <= 2021 or not 1 <= month <= 12:
            continue
        key = (int(year), int(month))
        if key in result:
            notes.append("Повтор месяца в исторических категориях нежилья; разрез недоступен.")
            return {}
        item = {"nonresidential": _cell_number(cells[3], notes)}
        item.update({field: _cell_number(cell, notes) for (field, _), cell in zip(HISTORICAL_FIELDS, cells[4:])})
        item["source"] = f"{HISTORY_SHEET}!I{cells[0].row}:O{cells[0].row}"
        subtotal = strict_sum(item[field] for field, _ in HISTORICAL_FIELDS)
        if subtotal is not None and item["nonresidential"] is not None and not math.isclose(subtotal, item["nonresidential"], abs_tol=.000001):
            notes.append(f"{item['source']}: сумма категорий отличается от итога; исходные значения не изменены.")
        result[key] = item
    return result


def read_breakdown_sources(book):
    housing_notes, historical_notes, modern_notes = [], [], []
    housing = _housing_history(book, housing_notes)
    nonresidential = _nonres_history(book, historical_notes)
    modern, groups = [], {}
    if OKS_SHEET not in book.sheetnames:
        modern_notes.append("Реестр ОКС отсутствует; структура с 2022 года недоступна.")
    else:
        rows = book[OKS_SHEET].iter_rows(min_row=1, max_col=46)
        headers = next(rows)
        expected = {1: "Назначение", 3: "Срок выдачи РС", 5: "Год выдачи",
                    16: "Общая площадь", 17: "Жилая площадь"}
        valid = all(normalized(headers[col].value) == normalized(label) for col, label in expected.items())
        classification = normalized(headers[45].value) == normalized("ФНО для Минстроя России")
        if not valid:
            modern_notes.append("Заголовки B/D/F/Q/R реестра ОКС не подтверждены; современная структура недоступна.")
        else:
            for cells in rows:
                purpose = normalized(cells[1].value)
                if purpose not in ("жилье", "нежилье"):
                    continue
                date = pd.to_datetime(cells[3].value, errors="coerce")
                fallback = number(cells[5].value)
                fallback_used = pd.isna(date) and fallback is not None
                if fallback_used:
                    date = pd.to_datetime(f"{int(fallback)}-01-01", errors="coerce")
                if pd.isna(date) or not 2022 <= date.year <= datetime.now().year:
                    continue
                source_row = cells[1].row
                general = _cell_number(cells[16], modern_notes, 1000)
                living = _cell_number(cells[17], modern_notes, 1000) if purpose == "жилье" else None
                label = " ".join(str(cells[45].value or "").split()) if classification else ""
                group = "native_" + hashlib.sha256(normalized(label).encode()).hexdigest()[:12] if label else "unknown"
                if purpose == "нежилье":
                    groups[group] = label or "Не указано в источнике"
                modern.append({"year": date.year, "month": date.month,
                               "kind": "housing" if purpose == "жилье" else "nonresidential",
                               "housing_total": general if purpose == "жилье" else None,
                               "living": living, "mop_embedded": _gap(general, living, modern_notes,
                                                                        f"{OKS_SHEET}!Q{source_row}:R{source_row}") if purpose == "жилье" else None,
                               "nonresidential": general if purpose == "нежилье" else None,
                               "group": group, "source_row": source_row, "fallback": fallback_used})
            if not classification:
                modern_notes.append("Заголовок AT не подтверждён; нежилые объекты показаны в неизвестной категории.")
    return {"housing": housing, "nonresidential": nonresidential, "modern": modern, "groups": groups,
            "housing_notes": housing_notes, "historical_notes": historical_notes, "modern_notes": modern_notes}


def _historical_ytd(data, month, fields, notes):
    result = {}
    for year in sorted({key[0] for key in data}):
        months = [data.get((year, m)) for m in range(1, month + 1)]
        if any(item is None for item in months):
            notes.append(f"{year}: неполное покрытие месяцев 1–{month}; структура за этот период не показана.")
            continue
        result[year] = {"year": year, **{field: strict_sum(item[field] for item in months) for field in fields}}
        notes.append(f"{year}, месяцы 1–{month}: " + "; ".join(item["source"] for item in months) + ".")
    return result


def _block(identifier, title, fields, rows, notes, month):
    aggregate = {"housing_components": "housing_total", "area_composition": "total",
                 "nonresidential_historical": "nonresidential", "nonresidential_native": "nonresidential"}[identifier]
    return {"id": identifier, "title": title, "unit": "тыс. м²", "periodLabel": MONTH_LABELS[month - 1],
            "columns": [{"id": "year", "label": "Год"}] + [{"id": key, "label": label} for key, label in fields],
            "rows": rows, "series": [{"id": key, "name": label,
                                      "points": [{"x": str(row["year"]), "y": row[key]} for row in rows]}
                                     for key, label in fields if key != aggregate],
            "notes": notes + (["Нет данных с подтверждённым покрытием выбранного периода."] if not rows else [])}


def breakdowns(data, kind, month):
    housing_notes = list(data["housing_notes"])
    history_notes = list(data["historical_notes"])
    modern_notes = list(data["modern_notes"])
    housing_fields = (("living", "Жилая площадь"), ("mop_embedded", "МОП и встроенное нежильё"),
                      ("housing_total", "Общая площадь жилья"))
    housing = _historical_ytd(data["housing"], month, [key for key, _ in housing_fields], housing_notes)
    historical_fields = (("nonresidential", "Нежильё, всего"),) + HISTORICAL_FIELDS
    historical = _historical_ytd(data["nonresidential"], month, [key for key, _ in historical_fields], history_notes)
    native = []
    for year in sorted({row["year"] for row in data["modern"]}):
        selected = [row for row in data["modern"] if row["year"] == year and row["month"] <= month]
        if not selected:
            continue
        h = [row for row in selected if row["kind"] == "housing"]
        n = [row for row in selected if row["kind"] == "nonresidential"]
        if h:
            housing[year] = {"year": year, **{key: strict_sum(row[key] for row in h) for key, _ in housing_fields}}
        if n:
            native.append({"year": year, "nonresidential": strict_sum(row["nonresidential"] for row in n),
                           **{group: strict_sum(row["nonresidential"] for row in n if row["group"] == group)
                              if any(row["group"] == group for row in n) else 0 for group in data["groups"]}})
        refs = ",".join(str(row["source_row"]) for row in selected)
        fallback = ",".join(str(row["source_row"]) for row in selected if row["fallback"])
        modern_notes.append(f"{year}, месяцы 1–{month}: Реестр ОКС, строки {refs}; D (дата), F (резервный год), "
                            "Q/R (м² / 1000), AT (исходная группа). Статус РС не фильтруется." +
                            (f" Год F вместо даты D, январь: строки {fallback}." if fallback else ""))
    basis = ["Исторические пары жилья Вспомогат1!C:D (м² / 1000) и категории нежилья "
             "Данные с 2011 года!I:O (тыс. м²) имеют собственную базу. Они могут отличаться от основной "
             "исторической таблицы; значения не масштабируются для совпадения.",
             "Все строки относятся к одним и тем же месяцам с начала года. Пустые исходные значения "
             "не заменяются нулями; неполная сумма показана как null."]
    result = []
    if kind in ("housing", "total"):
        result.append(_block("housing_components", "Структура площади жилья", housing_fields,
                             [housing[y] for y in sorted(housing)], basis + housing_notes + modern_notes +
                             ["МОП и встроенное нежильё = общая минус жилая площадь; отдельного источника МОП нет."], month))
    if kind in ("nonresidential", "total"):
        result.append(_block("nonresidential_historical", "Нежильё: исторические категории", historical_fields,
                             [historical[y] for y in sorted(historical)], basis + history_notes, month))
        native_fields = (("nonresidential", "Нежильё, всего"),) + tuple(sorted(data["groups"].items(), key=lambda item: item[1]))
        result.append(_block("nonresidential_native", "Нежильё: группы Минстроя", native_fields, native,
                             basis + modern_notes + ["Группы AT сохранены буквально. Исторические шесть категорий "
                             "не сопоставлены с современной классификацией; коды ОСМ и пустой AU не используются."], month))
    if kind == "total":
        nvalues = {year: row["nonresidential"] for year, row in historical.items()}
        nvalues.update({row["year"]: row["nonresidential"] for row in native})
        fields = (("living", "Жилая площадь"), ("mop_embedded", "МОП и встроенное нежильё"),
                  ("nonresidential", "Общая площадь нежилья"), ("total", "Общая площадь, всего"))
        rows = []
        for year in sorted(set(housing) | set(nvalues)):
            h = housing.get(year, {})
            row = {"year": year, "living": h.get("living"), "mop_embedded": h.get("mop_embedded"),
                   "nonresidential": nvalues.get(year)}
            row["total"] = strict_sum((row["living"], row["mop_embedded"], row["nonresidential"]))
            rows.append(row)
        result.insert(0, _block("area_composition", "Структура общей площади", fields, rows,
                               basis + housing_notes + history_notes + modern_notes +
                               ["Общая структура включает жилую площадь, МОП и встроенное нежильё, общую площадь "
                                "нежилья. Это не смешанный итог основной таблицы (жилая + нежилая)."], month))
    return result
