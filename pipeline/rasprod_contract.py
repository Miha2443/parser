"""Exact active API population and report content for Nashdom sales history."""
from __future__ import annotations

import math

from pipeline.downloaders.nashdom_rasprod_api import METRICS, SECTIONS

KPI_NAMES = {row[3].strip().casefold() for row in METRICS}
TABLES = dict(zip(("fed_okruga", "regions", "developers", "by_dev_volume", "by_population", "by_class"),
                 SECTIONS.values()))


def number(value):
    if isinstance(value, bool):
        raise ValueError("rasprod measurement is a boolean")
    try:
        result = float(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."))
    except (TypeError, ValueError) as exc:
        raise ValueError("rasprod measurement is missing or invalid") from exc
    if not math.isfinite(result) or result < 0:
        raise ValueError("rasprod measurement is missing or invalid")
    return result


def key(row):
    if not isinstance(row, dict):
        raise ValueError("rasprod API entry is not an object")
    region, year, month = row.get("region_key"), row.get("year"), row.get("month_num")
    if region not in {"rf", "msk"} or type(year) is not int or year <= 0 or type(month) is not int or not 1 <= month <= 12:
        raise ValueError("rasprod API entry has an invalid period")
    return region, year, month


def validate_entry(row):
    key(row)
    if row.get("api_verified") is not True:
        raise ValueError("rasprod API JSON lacks verified expected periods")
    metrics = row.get("kpi")
    if not isinstance(metrics, list) or len(metrics) != len(KPI_NAMES) or not all(isinstance(metric, dict) for metric in metrics):
        raise ValueError("rasprod API entry lacks all four KPIs")
    names = [str(metric.get("название", "")).strip().casefold() for metric in metrics]
    if set(names) != KPI_NAMES or len(set(names)) != len(names):
        raise ValueError("rasprod API entry lacks all four distinct KPIs")
    for metric in metrics:
        number(metric.get("значение"))
    tables = row.get("tables")
    if not isinstance(tables, dict) or set(tables) != set(TABLES.values()):
        raise ValueError("rasprod API entry lacks all six tables")
    for section, rows in tables.items():
        if not isinstance(rows, list) or not rows or not all(isinstance(item, dict) for item in rows):
            raise ValueError("rasprod API table is empty: " + section)
        if not any(item.get("наименование") and _measured(item.get("Объем жил. строительства")) for item in rows):
            raise ValueError("rasprod API table lacks measured rows: " + section)


def _measured(value):
    try:
        number(value)
        return True
    except ValueError:
        return False


def validate_history(entries, expected):
    if not isinstance(entries, list):
        raise ValueError("rasprod API source is not a list")
    actual = []
    for row in entries:
        validate_entry(row)
        actual.append(key(row))
    if len(set(actual)) != len(actual):
        raise ValueError("rasprod API JSON contains duplicate periods")
    if set(actual) != set(expected):
        raise ValueError("rasprod API JSON period set differs from confirmed API population")
