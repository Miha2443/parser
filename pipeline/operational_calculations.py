"""Operational commissioning with the exact existing RV/history calculations."""
from __future__ import annotations

import pandas as pd

from app.vvod_operational import MONTHS_RU, housing_ytd_table, nonres_ytd_table, quarter_tree
from pipeline.report_values import records

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


def catalog(data):
    rv = data["load_monitoring_2_0"].get("rv", pd.DataFrame())
    if rv.empty:
        raise ValueError("Monitoring RV is missing")
    current_year = int(pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").max())
    selected = rv[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").eq(current_year)]
    months = sorted({MONTHS_RU.get(str(value).strip().casefold())
                     for value in selected["Месяц ввода по Мосстату"]} - {None})
    date = pd.to_datetime(data["load_monitoring_operational_history"].get("source_date", ""), dayfirst=True, errors="coerce")
    closed = 12 if pd.notna(date) and date.month == 1 else int(date.month) - 1 if pd.notna(date) else 8
    default = max([m for m in months if m <= closed], default=min(max(months), 8)) if months else 8
    years = sorted(pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").dropna().astype(int).unique(), reverse=True)
    return {"months": [{"id": int(m), "label": MONTH_LABELS[m - 1]} for m in months or range(1, 13)],
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
        period = visible["За выбранный период, млн м²"].fillna(0)
        remainder = (visible["За год, млн м²"] - period).clip(lower=0).fillna(0)
        totals = visible["За год, млн м²"].where(visible["За год, млн м²"].notna(), visible["За выбранный период, млн м²"])
        tables.append({"id": key, "title": title, "columns": [{"id": col, "label": col} for col in frame.columns],
                       "rows": records(frame), "chart": {"x": [str(int(v)) for v in visible["Год"]],
                          "period": records(pd.DataFrame({"value": period})),
                          "remainder": records(pd.DataFrame({"value": remainder})),
                          "totals": records(pd.DataFrame({"value": totals})),
                          "growth": records(pd.DataFrame({"value": visible["Изменение к аналогичному периоду, %"]}))}})
    tree = quarter_tree(rv, year, quarter, cumulative=cumulative)
    return {"selection": {"month": month, "year": year, "quarter": quarter, "cumulative": cumulative,
                           "excludeMkd": exclude_mkd}, "periodLabel": MONTH_LABELS[month - 1],
            "currentYear": options["currentYear"], "region": "Москва", "tables": tables, "tree": tree,
            "treeRows": [{"id": key, "label": title, "value": tree[key]} for key, title in TREE_LABELS],
            "sourceDetails": {"file": history.get("source_file"), "date": history.get("source_date")},
            "notes": ["Исторические месяцы: лист «Данные с 2011 года» Мониторинга 2.0.",
                      "Исторические годовые значения: static_vvod_rs_2011_2025.xlsx.",
                      "Структура ввода — Москва, по реестру РВ; отсутствие месячной истории не означает нулевой ввод."]}
