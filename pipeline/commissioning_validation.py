"""Independent raw-cell oracles for category admission, without the data parser."""
from __future__ import annotations

import math
import re

import pandas as pd

# These are provider row meanings, not aliases for a computed residual.
LABELS = {
    "жилые дома построенные населением": "ИЖС",
    "построенные населением": "ИЖС",  # Explicit test/legacy category label.
    "жилые дома в сельской местности построенные населением": "ИЖС_сельское",
    "жилые здания многоквартирные": "МКД",
    "многоквартирные жилые дома": "МКД",
    "мкд": "МКД",
    "жилые здания": "Жилые_здания",
    "жилые здания жилые помещения в нежилых зданиях и жилые дома построенные населением": "TOTAL",
}
MONTHS = ("январь", "февраль", "март", "апрель", "май", "июнь", "июль", "август", "сентябрь", "октябрь", "ноябрь", "декабрь")


def text(value):
    return re.sub(r"\s+", " ", str(value).casefold().replace("ё", "е").replace(",", " ")).strip()


def number(value):
    if pd.isna(value):
        return None
    parsed = pd.to_numeric(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."), errors="coerce")
    if pd.isna(parsed):
        return None
    if not math.isfinite(parsed) or parsed < 0:
        raise ValueError("34118 raw oracle found an invalid measurement")
    return float(parsed)


def period(value):
    label = text(value).replace("–", "-").replace("—", "-")
    if not label or label == "nan":
        return "year", 12
    if label in MONTHS:
        return "month", MONTHS.index(label) + 1
    if label in {"i квартал", "ii квартал", "iii квартал", "iv квартал"}:
        return "quarter", ("i квартал", "ii квартал", "iii квартал", "iv квартал").index(label) * 3 + 3
    if label.startswith("январь-") and label[7:] in MONTHS:
        month = MONTHS.index(label[7:]) + 1
        return ("year" if month == 12 else "ytd"), month
    raise ValueError("34118 raw oracle found an unknown period label")


def raw_emiss_cells(path):
    """Map labelled input cells to independently specified category/period keys."""
    raw = pd.read_excel(path, sheet_name="Данные", header=None)
    year_row = None
    for index in range(min(8, len(raw) - 1)):
        years = pd.to_numeric(raw.iloc[index], errors="coerce")
        if years.between(2000, 2100).sum() >= 2:
            year_row = index
            break
    if year_row is None:
        raise ValueError("34118 raw oracle lacks year headers")
    columns, year = {}, None
    for column in range(raw.shape[1]):
        possible = pd.to_numeric(raw.iat[year_row, column], errors="coerce")
        if pd.notna(possible) and 2000 <= possible <= 2100 and possible == int(possible):
            year = int(possible)
        if year is not None:
            kind, month = period(raw.iat[year_row + 1, column])
            columns[column] = year, kind, month
    cells = {}
    for row in range(year_row + 2, len(raw)):
        label = text(raw.iat[row, 1])
        category = LABELS.get(label)
        if category is None:
            continue
        region_label = text(raw.iat[row, 0])
        region = "РФ" if "российская федерация" in region_label else "Москва" if "москва" in region_label else None
        if region is None:
            continue
        for column, (year, kind, month) in columns.items():
            value = number(raw.iat[row, column])
            if value is None:
                continue
            key = region, year, month, kind, category
            if key in cells:
                raise ValueError("34118 raw oracle found ambiguous duplicate category cells")
            cells[key] = {"value": value / 1000, "source_file": path.name, "source_row": row + 1,
                          "source_column": column + 1, "source_label": str(raw.iat[row, 1])}
    return cells


def validate_emiss_frame(path, frame):
    """TOTAL/rural IHS must not masquerade as national IHS or explicit MKD."""
    expected = raw_emiss_cells(path)
    indexed = frame.set_index(["region", "year", "month", "period_type"])
    for (region, year, month, kind, category), source in expected.items():
        key = region, year, month, kind
        if key not in indexed.index:
            raise ValueError("34118 category period disappeared from the parsed source")
        row = indexed.loc[key]
        actual = row.get(category)
        if (not isinstance(actual, (int, float)) or not math.isfinite(actual)
                or not math.isclose(actual, source["value"], rel_tol=1e-10, abs_tol=1e-8)):
            raise ValueError("34118 parsed category differs from the raw source cell: " + category)
        for suffix in ("source_file", "source_row", "source_column", "source_label"):
            if row.get(category + "_" + suffix) != source[suffix]:
                raise ValueError("34118 category provenance differs from the raw source cell: " + category)
    for key, row in indexed.iterrows():
        for category in set(LABELS.values()):
            if pd.notna(row.get(category)) and (*key, category) not in expected:
                raise ValueError("34118 contains a category not independently measured in this source: " + category)
    return expected


def validate_primary_rv(path, canonical):
    """Independently admit the complete physical RV population and raw areas."""
    from collections import defaultdict
    from pipeline.monitoring_registry import RV_FIELDS, normalized_cell, normalize_rv_years
    if not {"source_sheet", "source_row", "source_file", "Общая площадь", "Жилая площадь",
            "Общая площадь_measured", "Жилая площадь_measured", "category_жилое", "category_моп",
            "category_нежилое_в_жилом", "category_нежилое_отдельное"} <= set(canonical.columns):
        raise ValueError("Monitoring canonical RV lacks physical row provenance")
    sheets = {sheet: normalize_rv_years(frame) for sheet, frame in pd.read_excel(path, sheet_name=None).items()}
    raw = sheets["Реестр РВ"]
    visible = pd.to_numeric(raw["Год ввода по Мосстату"], errors="coerce").notna()
    if "Группа компаний" in raw:
        visible &= ~raw["Группа компаний"].astype(str).str.strip().str.casefold().eq("false")
    expected_rows = {int(index) + 2 for index in raw.index[visible]}
    primary = canonical.loc[canonical.source_sheet.eq("Реестр РВ")]
    if primary.source_row.duplicated().any() or set(primary.source_row) != expected_rows:
        raise ValueError("Monitoring canonical RV changed the physical primary population")
    if not canonical.source_file.eq(path.name).all():
        raise ValueError("Monitoring canonical RV has incorrect source file provenance")
    # Labels and period cells independently determine the categories/selection.
    # A correct area with a wrong subtype or year must not pass admission.
    def area(value):
        value = pd.to_numeric(str(value).replace("\xa0", "").replace(" ", "").replace(",", "."), errors="coerce")
        return float(value) if pd.notna(value) and math.isfinite(value) else None
    for _, chosen in canonical.iterrows():
        sheet = chosen.source_sheet
        if (sheet not in sheets or not math.isfinite(chosen.source_row) or chosen.source_row != int(chosen.source_row)
                or not 2 <= chosen.source_row <= len(sheets[sheet]) + 1):
            raise ValueError("Monitoring canonical RV has invalid physical source provenance")
        original = sheets[sheet].iloc[int(chosen.source_row) - 2]
        for field in RV_FIELDS[:-2]:
            if normalized_cell(chosen.get(field)) != normalized_cell(original.get(field)):
                raise ValueError("Monitoring canonical RV classification/period differs from its physical source row")
        for field in ("Общая площадь", "Жилая площадь"):
            measured = area(original.get(field))
            expected_area = measured if measured is not None else 0
            if not math.isclose(float(chosen[field]), expected_area, rel_tol=1e-10, abs_tol=1e-8):
                raise ValueError("Monitoring canonical RV area differs from its physical source row")
            if field + "_measured" in canonical and bool(chosen[field + "_measured"]) != (measured is not None and measured >= 0):
                raise ValueError("Monitoring canonical RV area measurement evidence differs from its raw source")
        general, living = area(original.get("Общая площадь")), area(original.get("Жилая площадь"))
        general, living = general if general is not None else 0, living if living is not None else 0
        residential = original.get("Отрасли") == "Жилые объекты"
        housing = residential and original.get("Группировка") == "Жилье"
        expected_categories = {"category_жилое": living if housing else 0,
                               "category_моп": max(general - living, 0) if housing else 0,
                               "category_нежилое_в_жилом": general if residential and original.get("Группировка") == "Нежилье" else 0,
                               "category_нежилое_отдельное": general if not residential else 0}
        for category, expected in expected_categories.items():
            if not math.isclose(float(chosen[category]), expected, rel_tol=1e-10, abs_tol=1e-8):
                raise ValueError("Monitoring canonical RV area category differs from its raw source: " + category)
    # Check source cells, not canonical areas that legitimately replace blank
    # metric cells by zero. Multiplicity is a capacity, never a global dedup key.
    def keys(frame):
        result = {}
        for index, row in frame.iterrows():
            key = tuple(normalized_cell(row.get(field)) for field in RV_FIELDS)
            if key[2] and any(key[position] for position in (0, 1, 7, 8)):
                result[int(index) + 2] = key
        return result
    references = list(keys(raw).values())
    supplements = canonical.loc[~canonical.source_sheet.eq("Реестр РВ")]
    required = {"Год ввода по Мосстату", "Общая площадь", "Группа компаний"}
    for sheet, raw_sheet in sheets.items():
        if sheet in {"Реестр РВ", "Реестр ОКС"} or not required <= set(raw_sheet.columns):
            continue
        source = raw_sheet
        source_keys = keys(source)
        selected = supplements.loc[supplements.source_sheet.eq(sheet)]
        selected_rows = set(selected.source_row)
        if selected.source_row.duplicated().any() or not set(selected.source_row) <= {int(index) + 2 for index in source.index}:
            raise ValueError("Monitoring supplement has invalid physical row provenance")
        # Independently derive the required population from raw physical cells.
        # Grouped multiset quotas reserve exact copies across the whole sheet;
        # neither canonical rows nor the selector decide the expected set.
        source_groups, reference_groups = defaultdict(list), defaultdict(list)
        for row, key in source_keys.items():
            source_groups[key].append(row)
        for slot, key in enumerate(references):
            reference_groups[key].append(slot)
        excluded, consumed = set(), set()
        for key, rows in source_groups.items():
            slots = reference_groups[key]
            quota = min(len(rows), len(slots))
            excluded.update(rows[:quota])
            consumed.update(slots[:quota])
        masks = {position: defaultdict(list) for position in (1, 7, 8)}
        for index, key in enumerate(references):
            for position in masks:
                if key[0] or (position != 1 and key[1]):
                    masks[position][key[:position] + key[position + 1:]].append(index)
        def candidates(key):
            return {index for position in masks for index in masks[position].get(key[:position] + key[position + 1:], [])}
        for row, key in source_keys.items():
            if row in excluded:
                continue
            matches = candidates(key)
            if len(matches) == 1 and not matches & consumed:
                excluded.add(row)
                consumed.update(matches)
        retained = {int(index) + 2 for index in source.index} - excluded
        allowed = pd.to_numeric(source["Год ввода по Мосстату"], errors="coerce").notna()
        allowed &= ~source["Группа компаний"].astype(str).str.strip().str.casefold().eq("false")
        required_rows = retained & {int(index) + 2 for index in source.index[allowed]}
        if required_rows - selected_rows:
            raise ValueError("Monitoring canonical RV is missing required historical physical rows: " + sheet)
        if selected_rows - required_rows:
            raise ValueError("Monitoring canonical RV contains a copied/superseded corrected or excluded historical row: " + sheet)
        # Reference capacity includes every retained raw row before downstream
        # filters, so another worksheet cannot resurrect an excluded record.
        references.extend(source_keys[row] for row in source_keys if row in retained)
    eligible = {sheet for sheet, frame in sheets.items() if sheet != "Реестр ОКС" and required <= set(frame.columns)}
    if not set(supplements.source_sheet) <= eligible:
        raise ValueError("Monitoring canonical RV references an unavailable RV worksheet")
    return {"primary_rows": len(primary), "supplement_rows": len(supplements)}
