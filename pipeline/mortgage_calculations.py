"""Validated monthly mortgage flows and comparable calendar-year summaries."""
from __future__ import annotations

import copy
from datetime import date, datetime
import math
import re


SOURCE_URL = "https://наш.дом.рф/аналитика/ипотечное_кредитование"
SOURCE_FILE = "data/raw/realty/nashdom/mortgage_history.json"
REGIONS = {"msk": "Москва", "rf": "Российская Федерация"}
SEGMENTS = ("all", "primary", "secondary")
METRICS = ("volumeRub", "count", "ratePct", "termMonths", "averageAmountRub")
MONTH_LABELS = ("Январь", "Январь–февраль", "Январь–март", "Январь–апрель",
                "Январь–май", "Январь–июнь", "Январь–июль", "Январь–август",
                "Январь–сентябрь", "Январь–октябрь", "Январь–ноябрь", "Январь–декабрь")


def _finite_number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    try:
        return math.isfinite(value)
    except OverflowError:
        return False


def validate_source(document):
    """Return a detached source snapshot, rejecting ambiguous or non-month data."""
    if not isinstance(document, dict) or type(document.get("schemaVersion")) is not int or document["schemaVersion"] != 1:
        raise ValueError("Unsupported mortgage source schema")
    if document.get("sourceUrl") != SOURCE_URL:
        raise ValueError("Unexpected mortgage source URL")
    collected = document.get("collectedAt")
    if not isinstance(collected, str):
        raise ValueError("Mortgage collection timestamp is missing")
    try:
        instant = datetime.fromisoformat(collected.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("Invalid mortgage collection timestamp") from exc
    if instant.tzinfo is None:
        raise ValueError("Mortgage collection timestamp must include a timezone")
    source_date = document.get("sourceDate")
    if source_date is not None:
        if not isinstance(source_date, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", source_date):
            raise ValueError("Invalid mortgage source date")
        date.fromisoformat(source_date)
    rows = document.get("records")
    if not isinstance(rows, list) or not rows:
        raise ValueError("Mortgage source has no observations")
    keys = set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("Invalid mortgage observation")
        if (not isinstance(row.get("region"), str) or row["region"] not in REGIONS
                or not isinstance(row.get("segment"), str) or row["segment"] not in SEGMENTS):
            raise ValueError("Invalid mortgage region or segment")
        period = row.get("period")
        if not isinstance(period, str) or not re.fullmatch(r"[1-9]\d{3}-(?:0[1-9]|1[0-2])", period):
            raise ValueError("Invalid mortgage observation month")
        if row.get("basis") != "month":
            raise ValueError("Mortgage observations must be monthly flows")
        key = (row["region"], period, row["segment"])
        if key in keys:
            raise ValueError("Duplicate mortgage observation")
        keys.add(key)
        for metric in METRICS:
            if metric not in row:
                raise ValueError(f"Mortgage metric {metric} is missing")
            value = row[metric]
            if value is not None and (not _finite_number(value) or value < 0):
                raise ValueError(f"Invalid mortgage metric {metric}")
    if not any(row[metric] is not None for row in rows for metric in METRICS):
        raise ValueError("Mortgage source has no measured values")
    if "notes" in document and (not isinstance(document["notes"], list)
                                 or any(not isinstance(note, str) for note in document["notes"])):
        raise ValueError("Invalid mortgage notes")
    if "coverage" in document and not isinstance(document["coverage"], dict):
        raise ValueError("Invalid mortgage coverage")
    return copy.deepcopy(document)


def region_periods(document):
    return {region: sorted({row["period"] for row in document["records"] if row["region"] == region
                            and any(row[metric] is not None for metric in METRICS)})
            for region in REGIONS}


def _year_sum(values, year, through):
    if through is None:
        return None
    months = [values.get((year, month)) for month in range(1, through + 1)]
    if any(value is None for value in months):
        return None
    try:
        total = sum(months) if all(isinstance(value, int) for value in months) else math.fsum(months)
    except OverflowError:
        return None
    return total if _finite_number(total) else None


def _change(value, previous):
    if value is None or previous in (None, 0):
        return None
    try:
        change = (value / previous - 1) * 100
    except OverflowError:
        return None
    return change if math.isfinite(change) else None


def _annual_metric(rows, years, metric, through):
    values = {(int(row["period"][:4]), int(row["period"][5:])): row[metric]
              for row in rows if row["segment"] == "all"}
    observed = [key for key, value in values.items() if value is not None]
    latest_year = max((key[0] for key in observed), default=None)
    summaries = {}
    for year in years:
        observed_months = [month for (row_year, month) in observed if row_year == year]
        target = min(through, max(observed_months)) if observed_months and year == latest_year else through
        actual = max((month for month in observed_months if month <= target), default=None)
        selected = _year_sum(values, year, target) if actual is not None else None
        annual = _year_sum(values, year, 12)
        summaries[year] = {"annual": annual, "selected": selected, "actualThroughMonth": actual,
                           "annualYoYPct": _change(annual, _year_sum(values, year - 1, 12)),
                           "selectedYoYPct": _change(selected, _year_sum(values, year - 1, actual))}
    return summaries


def build_report(document, region, month):
    """Use all-source totals directly; never infer a segment, rate or loan size."""
    if region not in REGIONS or type(month) is not int or not 1 <= month <= 12:
        raise ValueError("Invalid mortgage report filters")
    rows = [row for row in document["records"] if row["region"] == region]
    if not rows:
        raise ValueError("Mortgage region has no observations")
    periods = region_periods(document)[region]
    if not periods:
        raise ValueError("Mortgage region has no measured values")
    first_year, last_year = int(periods[0][:4]), int(periods[-1][:4])
    years = list(range(first_year, last_year + 1))
    volumes = _annual_metric(rows, years, "volumeRub", month)
    counts = _annual_metric(rows, years, "count", month)
    annual = [{"year": year, "volume": volumes[year], "count": counts[year]} for year in years]
    indexed = {(row["period"], row["segment"]): row for row in rows}
    start = int(periods[0][:4]) * 12 + int(periods[0][5:]) - 1
    end = int(periods[-1][:4]) * 12 + int(periods[-1][5:]) - 1
    monthly = []
    for serial in range(start, end + 1):
        year, zero_month = divmod(serial, 12)
        period = f"{year:04d}-{zero_month + 1:02d}"
        monthly.append({"period": period, **{
            segment: {metric: indexed.get((period, segment), {}).get(metric) for metric in METRICS}
            for segment in SEGMENTS}})
    return {"selection": {"region": region, "month": month}, "regionLabel": REGIONS[region],
            "annual": annual, "monthly": monthly, "notes": list(document.get("notes", []))}
