"""Operational commissioning with the exact existing RV/history calculations."""
from __future__ import annotations

import pandas as pd

from app.vvod_operational import MONTHS_RU, housing_ytd_table, nonres_ytd_table, quarter_tree
from pipeline.report_values import records
from pipeline.commissioning_structure import monthly_structure

MONTH_LABELS = ["Январь", "Январь–февраль", "Январь–март", "Январь–апрель", "Январь–май",
                "Январь–июнь", "Январь–июль", "Январь–август", "Январь–сентябрь",
                "Январь–октябрь", "Январь–ноябрь", "Январь–декабрь"]
TREE_LABELS = [("total", "Всего"), ("housing_objects", "Жилые объекты, общая площадь"),
               ("residential_area", "Жилая площадь"), ("mkd_total", "МКД, общая площадь"),
               ("mkd_residential", "Квартиры (жилая площадь МКД)"), ("izhs", "ИЖС"), ("mop", "МОП"),
               ("nonres_in_housing", "Нежилые помещения в жилых объектах"),
               ("nonres_objects", "Нежилые отдельно стоящие объекты"), ("offices", "Офисы"),
               ("hotels", "Гостиницы и апарт-отели"), ("industrial", "Промышленные"),
               ("social", "Социальные"), ("other", "Прочее"), ("nonres_total", "Всё нежильё")]


def _source_date(value):
    date = pd.to_datetime(value, format="ISO8601", errors="coerce")
    return date if pd.notna(date) else pd.to_datetime(value, dayfirst=True, errors="coerce")


def catalog(data):
    rv = data["load_monitoring_2_0"].get("rv", pd.DataFrame())
    if rv.empty:
        raise ValueError("Monitoring RV is missing")
    current_year = int(pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").max())
    selected = rv[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").eq(current_year)]
    months = sorted({MONTHS_RU.get(str(value).strip().casefold())
                     for value in selected["Месяц ввода по Мосстату"]} - {None})
    date = _source_date(data["load_monitoring_operational_history"].get("source_date"))
    available = [m for m in months if pd.isna(date) or (current_year, m) <= (date.year, date.month)]
    if available:
        default = max(available)
    elif pd.notna(date) and current_year == date.year:
        default = int(date.month)
    else:
        raise ValueError("Monitoring RV has no available month observations")
    years = sorted(pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").dropna().astype(int).unique(), reverse=True)
    # The YTD selector covers historical years, not just the latest RV year.
    return {"months": [{"id": m, "label": MONTH_LABELS[m - 1]} for m in range(1, 13)],
            "defaultMonth": default, "currentYear": current_year, "years": [int(year) for year in years]}


def report(data, options, month=None, exclude_mkd=False, year=None, quarter=1, cumulative=False):
    month = options["defaultMonth"] if month is None else month
    year = options["currentYear"] if year is None else year
    if month not in [item["id"] for item in options["months"]] or year not in options["years"] or quarter not in range(1, 5):
        raise LookupError("Operational period is unavailable")
    vvod, rv = data["load_vvod_static"], data["load_monitoring_2_0"]["rv"]
    history = data["load_monitoring_operational_history"]
    housing = housing_ytd_table(vvod["msk_total"], rv, history["housing_monthly"], month)
    nonres = nonres_ytd_table(vvod["msk_nonres"], rv, month, exclude_mkd=exclude_mkd, period_from_year=2022)
    tables = []
    for key, title, frame in [("housing", "Ввод жилья", housing), ("nonres", "Ввод нежилой недвижимости", nonres)]:
        frame = frame[frame["Год"].ge(2011)].reset_index(drop=True)
        visible = frame[frame[["За год, млн м²", "За выбранный период, млн м²"]].notna().any(axis=1)].copy()
        period = visible["За выбранный период, млн м²"]
        annual = visible["За год, млн м²"]
        # A known annual value remains visible in grey when its selected
        # period is unknown; the period itself must stay null.
        remainder = (annual - period).where(period.notna(), annual).clip(lower=0)
        totals = visible["За год, млн м²"].where(visible["За год, млн м²"].notna(), visible["За выбранный период, млн м²"])
        forecasts = data.get("load_commissioning_forecasts", {})
        forecast_field = "housing_living" if key == "housing" else "nonres_standalone" if exclude_mkd else "nonres_total"
        forecast_rows = [{"year": row["year"], "value": row[forecast_field] / 1000}
                         for row in forecasts.get("rows", []) if row.get(forecast_field) is not None and row[forecast_field] >= 0 and row["year"] >= options["currentYear"]]
        available_months = [MONTHS_RU.get(str(value).strip().casefold()) for value in
                            rv.loc[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").eq(options["currentYear"]),
                                   "Месяц ввода по Мосстату"]]
        latest_month = max((value for value in available_months if value), default=month)
        tables.append({"id": key, "title": title, "columns": [{"id": col, "label": col} for col in frame.columns],
                       "annualForecasts": forecast_rows,
                       "rows": records(frame), "chart": {"x": [str(int(v)) for v in visible["Год"]],
                          "actualMonths": [min(month, latest_month) if int(value) == options["currentYear"] else None for value in visible["Год"]],
                          "period": records(pd.DataFrame({"value": period})),
                          "remainder": records(pd.DataFrame({"value": remainder})),
                          "totals": records(pd.DataFrame({"value": totals})),
                          "growth": records(pd.DataFrame({"value": visible["Изменение к аналогичному периоду, %"]}))}})
    tree = quarter_tree(rv, year, quarter, cumulative=cumulative)
    previous_tree = quarter_tree(rv, year - 1, quarter, cumulative=cumulative) if year - 1 in options["years"] else None
    tree_growth = {key: (value - previous_tree[key]) / previous_tree[key] * 100
                   if previous_tree is not None and previous_tree[key] != 0 else None
                   for key, value in tree.items()}
    source_date = _source_date(history.get("source_date"))
    structure = monthly_structure(rv, year, month, source_date if pd.notna(source_date) else None)
    structure["sourceDate"] = history.get("source_date") or None
    return {"selection": {"month": month, "year": year, "quarter": quarter, "cumulative": cumulative,
                           "excludeMkd": exclude_mkd}, "periodLabel": MONTH_LABELS[month - 1],
            "currentYear": options["currentYear"], "region": "Москва", "tables": tables, "tree": tree,
            "treeGrowth": tree_growth,
            "structure": structure,
            "forecastSource": {key: value for key, value in data.get("load_commissioning_forecasts", {}).items() if key != "rows"},
            "treeRows": [{"id": key, "label": title, "value": tree[key]} for key, title in TREE_LABELS],
            "sourceDetails": {"file": history.get("source_file"), "date": history.get("source_date")},
            "notes": ["Исторические месяцы: лист «Данные с 2011 года» Мониторинга 2.0.",
                      "Исторические годовые значения: static_vvod_rs_2011_2025.xlsx.",
                      "Выбранный месяц задаёт границу суммирования, не подтверждает полноту месячной истории каждого года.",
                      "Текущий год: только имеющиеся строки РВ до выбранного месяца; отсутствующие поздние месяцы не прогнозируются.",
                      "Жильё: исторические месяцы суммируются как загружены; отсутствующая история сохраняется как null, годовые значения её не заменяют.",
                      "Нежильё: периодные значения рассчитываются по РВ только с 2022 года; до 2022 года они остаются null.",
                      "Структура ввода — Москва, по реестру РВ; отсутствие месячной истории не означает нулевой ввод.",
                      "Месячная структура сохраняет неизвестные площади как null, отдельно указывает известную сумму. "
                      "Прирост — сумма записей выбранного месяца. Реновация определяется по «Инвестор для Реновации» = да.",
                      "Для реконструируемых МКД с отсутствующей общей и заполненной жилой площадью применяется согласованный расчёт: "
                      "общая площадь = жилая / 0,58; МОП = расчётная общая − жилая. Заполненная общая площадь не заменяется. "
                      "Реконструкция определяется по наименованию объекта в столбце K; РВ 77-01-012565-2025 "
                      "(Космодамианская набережная) отдельно подтверждено пользователем. "
                      "Исходный реестр не изменяется; расчётные строки сохраняются в Excel на листе Area estimates.",
                      "Если после этого остаются неизвестные площади, выводится нижняя граница известной суммы (≥), "
                      "темп роста не вычисляется."] +
                     data.get("load_commissioning_forecasts", {}).get("notes", []) + structure["issues"]}
