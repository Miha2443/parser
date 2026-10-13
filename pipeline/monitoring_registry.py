"""Current RV rows take precedence over copied historical worksheet versions."""
from __future__ import annotations

from collections import Counter, defaultdict
from decimal import Decimal, InvalidOperation
import re
import unicodedata

import pandas as pd

RV_FIELDS = ("УИН", "№ РВ", "Год ввода по Мосстату", "Месяц ввода по Мосстату",
             "Отрасли", "Подтип объекта", "Группировка", "Общая площадь", "Жилая площадь")
CORRECTABLE = (1, 7, 8)  # Permit identifier, general area, living area.


def normalized_cell(value):
    if value is None or pd.isna(value):
        return None
    if isinstance(value, (pd.Timestamp,)):
        return ("date", value.isoformat())
    text = re.sub(r"\s+", " ", unicodedata.normalize("NFKC", str(value))).strip().casefold()
    if not text:
        return None
    try:
        number = Decimal(text.replace(" ", "").replace(",", "."))
        return ("number", number.normalize()) if number.is_finite() else None
    except InvalidOperation:
        return ("text", text)


def normalize_rv_years(frame):
    """Repair the documented 2018/19 shifted year/month cells, retaining indexes."""
    frame = frame.copy()
    if "Год ввода по Мосстату" not in frame:
        return frame
    raw = frame["Год ввода по Мосстату"]
    year = pd.to_numeric(raw, errors="coerce")
    shifted = (year.isna() | ~year.between(2000, 2100)) & raw.notna()
    if shifted.any() and "Дата ввода по Мосстату" in frame:
        dates = pd.to_datetime(frame.loc[shifted, "Дата ввода по Мосстату"], errors="coerce", dayfirst=True)
        serial = (dates - pd.Timestamp("1899-12-30")).dt.days
        recovered = serial.where(serial.between(2000, 2100))
        recovered = recovered.fillna(dates.dt.year.where(dates.dt.year.between(2000, 2100)))
        year.loc[shifted] = recovered
        if "Месяц ввода по Мосстату" in frame:
            months = raw.loc[shifted].where(pd.to_numeric(raw.loc[shifted], errors="coerce").isna())
            frame.loc[shifted, "Месяц ввода по Мосстату"] = months.combine_first(frame.loc[shifted, "Месяц ввода по Мосстату"])
    frame["Год ввода по Мосстату"] = year
    return frame


def _key(row):
    key = tuple(normalized_cell(row.get(field)) for field in RV_FIELDS)
    # A copied physical row may have no area (for example roads). Its full
    # compound identity still participates before downstream metric filters.
    return key if key[2] and any(key[index] for index in (0, 1, 7, 8)) else None


def select_rv_supplements(primary, supplements):
    """Return all primary rows and only independently admitted historical rows.

    Matching precedes every consumer filter. All exact copies reserve their
    one-to-one reference capacity before corrected versions are considered.
    Corrected versions require one reference row and differ in exactly one
    allowed field. Ambiguity retains the historical row and is reported.
    Internal duplicate rows in the primary worksheet are never collapsed.
    """
    primary = normalize_rv_years(primary)
    accepted = [primary]
    audit = {"primary_rows": len(primary), "supplements": {}}
    references = [_key(row) for _, row in primary.iterrows()]
    for sheet, raw in supplements:
        historical = normalize_rv_years(raw)
        exact = defaultdict(list)
        corrections = {position: defaultdict(list) for position in CORRECTABLE}
        for index, key in enumerate(references):
            if key is None:
                continue
            exact[key].append(index)
            for position in CORRECTABLE:
                # Correcting a permit requires an independent UIN; correcting
                # area requires a UIN or a nonempty permit plus classification.
                if key[0] or (position != 1 and key[1]):
                    corrections[position][key[:position] + key[position + 1:]].append(index)
        used, removed = set(), set()
        counts = Counter(exact=0, corrected=0, ambiguous=0, retained=0)
        historical_keys = [_key(row) for _, row in historical.iterrows()]
        # A corrected row encountered first must not consume the capacity of
        # an exact copy appearing later in this same worksheet.
        for offset, key in enumerate(historical_keys):
            candidates = [index for index in exact.get(key, []) if index not in used] if key else []
            if candidates:
                used.add(candidates[0])
                removed.add(offset)
                counts["exact"] += 1
        for offset, key in enumerate(historical_keys):
            if offset in removed:
                continue
            candidates = set()
            if key:
                for position in CORRECTABLE:
                    candidates.update(corrections[position].get(key[:position] + key[position + 1:], []))
            if len(candidates) == 1 and not candidates & used:
                used.update(candidates)
                removed.add(offset)
                counts["corrected"] += 1
                continue
            if candidates:
                counts["ambiguous"] += 1
            counts["retained"] += 1
        kept = [offset for offset in range(len(historical)) if offset not in removed]
        selected = historical.iloc[kept].copy()
        accepted.append(selected)
        references.extend(_key(row) for _, row in selected.iterrows())
        audit["supplements"][sheet] = dict(counts)
    return accepted, audit
