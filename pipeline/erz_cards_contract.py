"""ERZ card coverage binds the workbook to the actual selected card URLs."""
from __future__ import annotations

import math
import re
from urllib.parse import parse_qs, urlparse


def validate_selection(payload):
    if not isinstance(payload, dict) or payload.get("region") != "rf":
        raise ValueError("ERZ card targets need the RF current-construction TOP selection")
    selection = payload.get("selection")
    if (not isinstance(selection, dict) or selection.get("region") != "rf"
            or selection.get("sorting") != "obyem_stroitelstva"):
        raise ValueError("ERZ card target TOP selection is missing/incorrect")
    url = urlparse(str(selection.get("url", "")))
    query = parse_qs(url.query)
    if (url.scheme != "https" or url.hostname not in {"erzrf.ru", "www.erzrf.ru"}
            or url.path != "/top-zastroyshchikov/rf" or query.get("topType") != ["0"]
            or query.get("regionKey") != ["0"]):
        raise ValueError("ERZ card target TOP URL does not match its selection")
    return selection


def identity(url):
    if not isinstance(url, str):
        raise ValueError("ERZ card URL is missing")
    parsed = urlparse(url)
    if (parsed.scheme != "https" or parsed.hostname not in {"erzrf.ru", "www.erzrf.ru"}
            or parsed.username or parsed.password or parsed.port not in {None, 443}
            or not parsed.path.startswith("/zastroyschiki/")):
        raise ValueError("ERZ card URL has an unexpected source origin/path")
    source_id = parsed.path.removeprefix("/zastroyschiki/").strip("/")
    if not source_id:
        raise ValueError("ERZ card URL has no source ID")
    return source_id


def target_keys(developers):
    if not isinstance(developers, list) or not developers:
        raise ValueError("ERZ card target population is empty")
    targets, ids, urls = [], set(), set()
    for developer in developers:
        if not isinstance(developer, dict) or not str(developer.get("name") or "").strip():
            raise ValueError("ERZ card target has no name")
        url = developer.get("card_url")
        source_id = identity(url)
        if source_id in ids or url in urls:
            raise ValueError("ERZ card target population has duplicate/ambiguous identities")
        ids.add(source_id)
        urls.add(url)
        targets.append({"source_id": source_id, "card_url": url})
    return targets


def number(value):
    if isinstance(value, bool) or value is None:
        return None
    try:
        result = float(re.sub(r"\s+", "", str(value)).replace(",", "."))
    except ValueError:
        return None
    return result if math.isfinite(result) and result >= 0 else None


def measured_columns(row):
    return [column for column in row if column == "Строится_м²"
            or re.fullmatch(r"Сдано_\d{4}_м²", str(column))]


def validate_rows(rows, expected):
    if not isinstance(rows, list) or not rows:
        raise ValueError("ERZ cards have no measured rows")
    actual, ids = [], set()
    for row in rows:
        if not isinstance(row, dict) or not str(row.get("name_card") or "").strip():
            raise ValueError("ERZ card has no parsed name")
        url = row.get("url")
        source_id = identity(url)
        if source_id != str(row.get("source_id", "")) or source_id in ids:
            raise ValueError("ERZ cards have missing/duplicate/incorrect source identities")
        ids.add(source_id)
        page_url = row.get("page_url")
        if not page_url or identity(page_url) != source_id:
            raise ValueError("ERZ card page does not match the requested target")
        if not any(number(row[column]) is not None for column in measured_columns(row)):
            raise ValueError("ERZ card has no measured construction/commissioning area")
        actual.append({"source_id": source_id, "card_url": url})
    def keys(items):
        return {(row["source_id"], row["card_url"]) for row in items}
    if len(actual) != len(expected) or keys(actual) != keys(expected):
        raise ValueError("ERZ cards do not cover the exact target population")
    return actual


def validate_workbook(path, expected):
    import pandas as pd
    with pd.ExcelFile(path) as workbook:
        if "failed" in workbook.sheet_names and not pd.read_excel(workbook, sheet_name="failed").empty:
            raise ValueError("ERZ card workbook contains failed targets")
        rows = pd.read_excel(workbook, sheet_name="cards", keep_default_na=False).to_dict("records")
    return rows, validate_rows(rows, expected)
