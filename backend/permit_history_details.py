"""Packaged object-level history for exports only; never a graph data source."""
from __future__ import annotations

import copy
from datetime import date, datetime
import gzip
import hashlib
from io import BytesIO
import json
import math
from pathlib import Path
import re

from openpyxl import load_workbook


SHEET = "Сроки строительства1"
WIDTH = 65
DEFAULT_PATH = Path(__file__).resolve().parent / "data" / "permit_history_2011_2021.json.gz"
EXPECTED_HEADERS = {1: "УИН", 2: "ОСМ_код", 15: "Первая РС дата",
                    17: "Последняя РС дата", 29: "Общая пл", 30: "Жилая пл"}


def _positive_area(value):
    return (isinstance(value, (int, float)) and not isinstance(value, bool)
            and math.isfinite(value) and value > 0)


def _eligible(values):
    when = values[17]
    return (isinstance(when, datetime) and 2011 <= when.year <= 2021
            and any(_positive_area(values[column]) for column in (29, 30)))


def _housing(value):
    if isinstance(value, str):
        return re.match(r"^01(?:\s|$)", value.strip()) is not None
    return not isinstance(value, bool) and value == 1


def _encode(value):
    if isinstance(value, datetime):
        return {"type": "datetime", "value": value.isoformat()}
    if isinstance(value, date):
        return {"type": "date", "value": value.isoformat()}
    if isinstance(value, float) and not math.isfinite(value):
        return {"type": "float", "value": "nan" if math.isnan(value) else "inf" if value > 0 else "-inf"}
    if value is None or isinstance(value, (str, bool, int, float)):
        return value
    raise ValueError(f"Unsupported source cell type: {type(value).__name__}")


def _decode(value):
    if not isinstance(value, dict):
        return value
    if set(value) != {"type", "value"}:
        raise ValueError("Invalid typed history cell")
    if value["type"] == "datetime":
        return datetime.fromisoformat(value["value"])
    if value["type"] == "date":
        return datetime.combine(date.fromisoformat(value["value"]), datetime.min.time())
    if value["type"] == "float" and value["value"] in ("nan", "inf", "-inf"):
        return float(value["value"])
    raise ValueError("Unknown typed history cell")


def _validate_headers(headers):
    if len(headers) != WIDTH or any(headers[i] != label for i, label in EXPECTED_HEADERS.items()):
        raise ValueError("Historical source A:BM headers do not match the expected schema")


def extract_history(source: Path):
    """Extract a stable, cached-value snapshot without evaluating workbook formulas."""
    source = Path(source)
    before = source.stat()
    raw = source.read_bytes()
    digest = hashlib.sha256(raw).hexdigest()
    book = load_workbook(BytesIO(raw), read_only=True, data_only=True)
    try:
        if SHEET not in book.sheetnames:
            raise ValueError(f"Missing source sheet: {SHEET}")
        iterator = book[SHEET].iter_rows(min_col=1, max_col=WIDTH, values_only=True)
        headers = list(next(iterator, ()))
        _validate_headers(headers)
        records = []
        for row_number, row in enumerate(iterator, 2):
            values = list(row)
            if _eligible(values):
                records.append({"source_row": row_number, "values": values})
    finally:
        book.close()
    after = source.stat()
    if ((before.st_size, before.st_mtime_ns, before.st_ctime_ns, before.st_ino)
            != (after.st_size, after.st_mtime_ns, after.st_ctime_ns, after.st_ino)
            or hashlib.sha256(source.read_bytes()).hexdigest() != digest):
        raise RuntimeError("Historical source changed during import")
    records.sort(key=lambda row: (row["values"][17].year, row["values"][17], row["source_row"]))
    return {"schema": 1, "headers": [_encode(v) for v in headers],
            "records": [{"source_row": row["source_row"], "values": [_encode(v) for v in row["values"]]}
                        for row in records],
            "source": {"file": source.name, "sheet": SHEET, "sha256": digest,
                       "date_semantics": "R: Последняя РС дата; latest recorded permit, not first issuance or snapshot date",
                       "area_columns": {"AD": "Общая пл", "AE": "Жилая пл"},
                       "import_filter": "R year 2011-2021 and positive finite numeric AD or AE",
                       "cell_values": "Saved data_only values; formulas are not recalculated",
                       "scope": "Export object details only; not graph inputs"}}


def write_history(payload, destination: Path):
    raw = json.dumps(payload, ensure_ascii=False, allow_nan=False, sort_keys=True,
                     separators=(",", ":")).encode("utf-8")
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_bytes(gzip.compress(raw, compresslevel=9, mtime=0))


class PermitHistoryDetails:
    def __init__(self, path: Path | None = None):
        self.path = Path(path) if path is not None else DEFAULT_PATH

    def select(self, permit_kind, *, month_from, month_to, year_from, year_to):
        if permit_kind not in ("housing", "nonresidential", "total"):
            raise ValueError("Invalid permit kind")
        if any(isinstance(v, bool) or not isinstance(v, int) or not 1 <= v <= 12
               for v in (month_from, month_to)) or month_from > month_to:
            raise ValueError("Invalid month interval")
        if any(isinstance(v, bool) or not isinstance(v, int) for v in (year_from, year_to)):
            raise ValueError("Invalid year interval")
        payload = json.loads(gzip.decompress(self.path.read_bytes()))
        if payload.get("schema") != 1:
            raise ValueError("Unsupported historical detail schema")
        headers = [_decode(v) for v in payload["headers"]]
        _validate_headers(headers)
        selected = []
        for record in payload["records"]:
            values = [_decode(v) for v in record["values"]]
            if len(values) != WIDTH or not _eligible(values):
                raise ValueError("Invalid packaged historical detail row")
            when = values[17]
            housing = _housing(values[2])
            if (year_from <= when.year <= year_to and month_from <= when.month <= month_to
                    and (permit_kind == "total" or housing == (permit_kind == "housing"))):
                selected.append((when.year, when, record["source_row"], values))
        selected.sort(key=lambda row: row[:3])
        return {"headers": headers, "rows": [row[3] for row in selected],
                "source_rows": [row[2] for row in selected], "source": copy.deepcopy(payload["source"]),
                "selection": {"permit_kind": permit_kind, "month_from": month_from, "month_to": month_to,
                              "year_from": year_from, "year_to": year_to},
                "notes": ["Historical object details only: years 2011-2021 by latest recorded permit date R.",
                          "Housing is OSM code 01; all other codes are nonresidential, including infrastructure.",
                          "No status filter; at least one of AD/AE is a positive finite numeric value.",
                          "Original A:BM saved values are preserved; missing areas are not replaced with zero."]}
