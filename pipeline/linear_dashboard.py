"""Measured linear commissioning history, separate from the legacy quarterly API."""
from __future__ import annotations

from datetime import date
import math


METRICS = (
    ("roads", "Дороги", "км", "дороги", "1.1"),
    ("bridges", "Мосты, тоннели, эстакады", "ед.", "мосты, тоннели, эстакады", "1.2"),
    ("crossings", "Пешеходные переходы", "ед.", "пешеходные переходы", "1.3"),
    ("metro_stations", "Станции метро", "ед.", "станции метро", None),
    ("metro_tunnels", "Тоннели метро", "км", "тоннели метро", None),
    ("depots", "Электродепо", "ед.", "электродепо", None),
)
HISTORY_SHEET = "Данные с 2011 года"


def number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) and result >= 0 else None


def norm(value):
    return " ".join(str(value or "").casefold().split())


def read_history(book):
    """Read at most 600 x 128 cells, only inside the uniquely named block.

    Do not count permits/objects from other sheets or infer depot totals from
    individual buildings. Years and rows come from headers, not a fixed axis.
    """
    if HISTORY_SHEET not in book.sheetnames:
        return [], ["Лист годовой истории линейных объектов отсутствует."]
    rows = list(book[HISTORY_SHEET].iter_rows(max_row=600, max_col=128, values_only=True))
    starts = [(i, j) for i, row in enumerate(rows) for j, value in enumerate(row[:8])
              if norm(value) == "ввод линейные объекты"]
    if not starts:
        return [], ["Именованный блок истории линейных объектов отсутствует."]
    if len(starts) != 1:
        raise ValueError("Ambiguous linear history block")
    start, label_col = starts[0]
    if start + 1 >= len(rows):
        raise ValueError("Linear history header is unavailable")
    header = rows[start + 1]
    if norm(header[label_col]) != "показатель":
        raise ValueError("Linear history header is unavailable")
    years = {}
    for col in range(label_col + 2, len(header)):
        value = number(header[col])
        if value is not None and value.is_integer() and 1900 <= value <= 2200:
            year = int(value)
            if year in years:
                raise ValueError("Duplicate linear history year")
            years[year] = col
    if not years:
        return [], ["В блоке линейных объектов нет годовых заголовков."]
    labels = {label: (key, unit) for key, _, unit, label, _ in METRICS}
    found, result = set(), []
    for row in rows[start + 2:start + 22]:
        label = norm(row[label_col])
        if not label:
            break
        if label not in labels:
            break
        key, unit = labels[label]
        if key in found or norm(row[label_col + 1]).rstrip(".") != unit.rstrip("."):
            raise ValueError("Duplicate linear history metric or incompatible unit")
        found.add(key)
        for year, col in sorted(years.items()):
            result.append({"id": key, "year": year, "value": number(row[col])})
    return result, []


def build_dashboard(dsti, history, forecasts, sources, today: date):
    current = today.year
    source_by_id = {source["id"]: source for source in sources}
    monitoring_date = source_by_id["monitoring"]["date"]
    dsti_date = source_by_id["dsti"]["date"]
    # A past-year snapshot can still be incomplete. Only years before its
    # publication year are treated as annual history.
    history_year = int(monitoring_date[:4]) if monitoring_date else None
    forecast_rows = {row["year"]: row for row in forecasts.get("rows", [])}
    result = []
    for key, label, unit, _, code in METRICS:
        measured = {}
        plans = {}
        observed_years = {row["year"] for row in history if row["id"] == key and row["year"] <= current}
        if code is not None and dsti is not None:
            for year, group in dsti[dsti["code"].eq(code)].groupby("year"):
                year = int(year)
                if year > current:
                    continue
                if any(norm(value).rstrip(".") != unit.rstrip(".") for value in group["unit"]):
                    raise ValueError("Incompatible DSTI unit for linear dashboard")
                observed_years.add(year)
                by_q = {int(row.quarter): row for row in group.itertuples()}
                if set(by_q) == {1, 2, 3, 4} and all(number(row.plan) is not None for row in by_q.values()):
                    plans[year] = sum(number(row.plan) for row in by_q.values())
                last = 0
                max_quarter = (today.month - 1) // 3 + 1 if year == current else 4
                for quarter in range(1, max_quarter + 1):
                    if quarter not in by_q or number(by_q[quarter].fact) is None:
                        break
                    last = quarter
                if not last or year < current and last < 4:
                    continue
                annual = last == 4 and year < current
                measured[year] = {
                    "fact": sum(number(by_q[q].fact) for q in range(1, last + 1)),
                    "factKind": "annual" if annual else "partial",
                    "factPeriod": f"За {year} год" if annual else f"1–{last} кв. {year} · неполный год",
                    "factSource": "dsti",
                }
        for row in history:
            year, value = row["year"], row["value"]
            if row["id"] != key or year > current or value is None or history_year is None or year > history_year:
                continue
            annual = year < min(current, history_year)
            # Never relabel an old partial-year snapshot as a completed year.
            if year < current and not annual:
                continue
            prior = measured.get(year)
            if prior and (dsti_date or "") > (monitoring_date or ""):
                continue
            measured[year] = {"fact": value, "factKind": "annual" if annual else "partial",
                              "factPeriod": f"За {year} год" if annual else f"На {monitoring_date} · неполный год",
                              "factSource": "monitoring"}
        forecast_years = {year for year, row in forecast_rows.items()
                          if year >= current and number(row.get(key)) is not None}
        years = sorted(observed_years | set(measured) | set(plans) | forecast_years)
        yearly = [{"year": year, **measured.get(year, {"fact": None, "factKind": None,
                    "factPeriod": None, "factSource": None}), "plan": plans.get(year),
                   "forecast": number(forecast_rows.get(year, {}).get(key)) if year >= current else None}
                  for year in years]
        fact = measured.get(current, {})
        plan, forecast = plans.get(current), number(forecast_rows.get(current, {}).get(key))
        target = plan if plan is not None else forecast
        value = fact.get("fact")
        percent = value / target * 100 if value is not None and target is not None and target > 0 else None
        result.append({"id": key, "label": label, "unit": unit, "fact": value,
                       "factPeriod": fact.get("factPeriod"), "factSource": fact.get("factSource"),
                       "annualPlan": plan, "forecast": forecast, "target": target,
                       "targetKind": "plan" if plan is not None else "forecast" if forecast is not None else None,
                       "percent": percent,
                       "remaining": max(0, target - value) if target is not None and value is not None else None,
                       "years": yearly})
    return {"currentYear": current, "metrics": result}
