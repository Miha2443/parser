"""UI-independent, literal page-6 selections; no rescaling or source writes."""
from __future__ import annotations

import math

import pandas as pd

from pipeline.data_access import MONTH_NAMES_RU

REGION_LABELS = {"msk": "Город Москва", "rf": "Российская Федерация"}
KPI_DEFINITIONS = (
    ("volume", "Объем жилищного строительства", "Объём жил. строительства", "тыс. м²", 0),
    ("sold", "Распроданность", "Распроданность", "%", 0),
    ("ready", "Стройготовность", "Стройготовность", "%", 0),
    ("ratio", "Отношение", "Отношение распроданности к стройготовности", "%", 0),
)
SECTION_TABS = (
    ("fed_okruga", "Федеральные округа"),
    ("regions", "Регионы"),
    ("developers", "Девелоперы"),
    ("by_dev_volume", "По объёму строительства"),
    ("by_population", "По численности населения"),
    ("by_class", "По классу недвижимости"),
)


def number(value):
    if value is None or pd.isna(value):
        return None
    value = float(value)
    return value if math.isfinite(value) else None


def raw_value(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, str):
        return value
    value = value.item() if hasattr(value, "item") else value
    return None if isinstance(value, float) and not math.isfinite(value) else value


def period_record(year, month):
    return {"id": f"{year:04d}-{month:02d}", "label": f"{MONTH_NAMES_RU[month - 1].capitalize()} {year}"}


def catalog_regions(data):
    return [{"id": region, "label": label,
             "periods": [period_record(*period) for period in data["periods_by_region"].get(region, [])]}
            for region, label in REGION_LABELS.items()
            if region in data["regions_available"] and data["periods_by_region"].get(region)]


def table_columns(frame, title):
    original = [column for column in frame.columns
                if column not in ("region_key", "year", "month", "month_name", "report_period", "section", "наименование")
                and not column.endswith("_num")]
    labels = {"наименование": title.rstrip("ы").rstrip("а").rstrip("ы") or "Сегмент"}
    for column in original:
        if "Объем" in column or "Объём" in column:
            labels[column] = "Объём, м²"
        elif "Распроданность" in column:
            labels[column] = "Распроданность"
        elif "Стройготовность" in column:
            labels[column] = "Стройготовность"
        elif "Отношение" in column:
            labels[column] = "Отношение Р / С"
        else:
            labels[column] = column
    return [{"id": column, "label": labels[column]} for column in ["наименование", *original]]


def sales_detail(data, region, year, month):
    kpi = data["kpi"]
    selected = kpi[(kpi["region_key"] == region) & (kpi["year"] == year) & (kpi["month"] == month)]
    metrics = []
    for identifier, substring, label, unit, digits in KPI_DEFINITIONS:
        rows = selected[selected["название"].str.contains(substring, na=False)]
        metrics.append({"id": identifier, "label": label, "unit": unit, "digits": digits,
                        "value": number(rows.iloc[0]["значение_num"]) if not rows.empty else None})

    forecast_columns = [column for column in kpi.columns if column.startswith("прогноз_") and not column.endswith("_num")]
    forecast = []
    for index, (_, row) in enumerate(selected.iterrows()):
        # The source's 'единица' can contain sold area, not the forecast's unit.
        unit = next((unit for _, substring, _, unit, _ in KPI_DEFINITIONS if substring in row["название"]), "")
        forecast.append({"id": f"forecast_{index}", "name": row["название"], "unit": unit,
                         "points": [{"x": column.replace("прогноз_", ""), "y": number(row.get(f"{column}_num"))}
                                    for column in forecast_columns]})
    period = period_record(year, month)
    charts = [{"id": "forecast", "title": f"Прогноз ввода по годам — {REGION_LABELS[region]}, {period['label']}",
               "kind": "bar", "unit": "", "series": forecast}]

    time_series = kpi[kpi["region_key"] == region].copy()
    time_series["period"] = pd.to_datetime(
        time_series["year"].astype(str) + "-" + time_series["month"].astype(str).str.zfill(2) + "-01")
    time_series = time_series.sort_values("period")

    def points(frame):
        return [{"x": row["period"].strftime("%Y-%m"), "y": number(row["значение_num"])}
                for _, row in frame.iterrows()]

    volume = time_series[time_series["название"].str.contains("Объем жилищного", na=False)]
    if not volume.empty:
        charts.append({"id": "monthly_volume", "title": "Объём жилищного строительства, тыс. м²",
                       "kind": "line", "unit": "тыс. м²",
                       "series": [{"id": "volume", "name": "Объём строительства", "unit": "тыс. м²", "points": points(volume)}]})
    percentages = []
    for identifier, substring, label, unit, _ in KPI_DEFINITIONS[1:]:
        rows = time_series[time_series["название"].str.contains(substring, na=False)]
        if not rows.empty:
            percentages.append({"id": identifier, "name": label, "unit": unit, "points": points(rows)})
    charts.append({"id": "monthly_percent", "title": "Распроданность · Стройготовность · Отношение, %",
                   "kind": "line", "unit": "%", "series": percentages})
    tables = []
    for identifier, title in SECTION_TABS:
        frame = data.get(identifier, pd.DataFrame())
        columns, rows = [], []
        if not frame.empty:
            frame = frame[(frame["region_key"] == region) & (frame["year"] == year) & (frame["month"] == month)]
            columns = table_columns(frame, title)
            keys = [column["id"] for column in columns]
            rows = [{key: raw_value(value) for key, value in zip(keys, values)}
                    for values in frame[keys].itertuples(index=False, name=None)]
        tables.append({"id": identifier, "title": title, "columns": columns, "rows": rows})
    return {"region": {"id": region, "label": REGION_LABELS[region]}, "period": period,
            "metrics": metrics, "charts": charts, "tables": tables}
