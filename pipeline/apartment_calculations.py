"""Pure calculations from legacy apartment pages 4 and 5; no I/O or name aliases."""
from __future__ import annotations

import math

import pandas as pd

REGION_LABELS = {"msk": "Город Москва", "rf": "Российская Федерация"}
ROOM_COLUMNS = [
    ("1 комн", "доля_1комн_%_num"), ("2 комн", "доля_2комн_%_num"),
    ("3 комн", "доля_3комн_%_num"), ("4+ комн", "доля_4+комн_%_num"),
]
NAME = "наименование"
AREA = "площадь_тыс_м²_num"
QUANTITY = "квартиры_тыс_шт_num"
MOSCOW_NAMES = ["город москва", "г. москва", "москва"]


def number(value):
    value = pd.to_numeric(value, errors="coerce")
    return float(value) if pd.notna(value) and math.isfinite(float(value)) else None


def available_regions(data):
    return [key for key in REGION_LABELS if key in data["regions_available"]]


def regional_frame(data, table, region):
    frame = data[table]
    return frame[frame["region_key"] == region].copy() if not frame.empty else frame.copy()


def developer_frame(data, region):
    frame = regional_frame(data, "developers", region)
    if not frame.empty:
        # Default pandas sorting (including ties/NaNs) is the legacy rule.
        frame = frame.sort_values(AREA, ascending=False).reset_index(drop=True)
        frame["place"] = frame.index + 1
    return frame


def region_frame(data, region):
    source = data["regions"]
    frame = regional_frame(data, "regions", region)
    if source.empty:
        return frame
    moscow_mask = source[NAME].astype(str).str.strip().str.casefold().isin(MOSCOW_NAMES)
    if region == "msk":
        moscow = source[source["region_key"].eq("rf") & moscow_mask]
        frame = pd.concat([moscow, frame], ignore_index=True).drop_duplicates(NAME, keep="first")
    frame = frame.sort_values(AREA, ascending=False)
    first = frame[NAME].astype(str).str.strip().str.casefold().isin(MOSCOW_NAMES)
    return pd.concat([frame[first], frame[~first]])


def rooms(row):
    # Raw percentage values, not re-normalized strip widths; missing stays null.
    return [{"type": label, "sharePercent": number(row[column])} for label, column in ROOM_COLUMNS]


def table_row(row):
    result = {"id": row[NAME], "name": row[NAME],
              "apartmentThousandCount": number(row[QUANTITY]),
              "areaThousandM2": number(row[AREA]), "rooms": rooms(row)}
    if "place" in row:
        result["place"] = int(row["place"])
    return result


def overview(data, region):
    apartments = regional_frame(data, "apartments", region)
    distribution = regional_frame(data, "distribution", region)
    developers = developer_frame(data, region)
    regions = region_frame(data, region)
    return {
        "region": region,
        "apartments": [{"type": row["тип"], "count": number(row["количество_шт_num"]),
                        "areaThousandM2": number(row[AREA])} for _, row in apartments.iterrows()],
        "distribution": [{"range": row["диапазон"], "sharePercent": number(row["доля_num"])}
                         for _, row in distribution.iterrows()],
        "developers": [table_row(row) for _, row in developers.iterrows()],
        "regions": [table_row(row) for _, row in regions.iterrows()],
        "developerCount": len(developers), "regionCount": len(regions),
    }


def developer_detail(data, region, developer):
    developers = developer_frame(data, region)
    selected = developers[developers[NAME] == developer]
    if selected.empty:
        raise LookupError("Unknown apartment developer")
    row = selected.iloc[0]
    qty, area = row[QUANTITY], row[AREA]
    # NaN is truthy in the legacy condition; its resulting KPI becomes JSON null.
    qty = float(qty) if pd.notna(qty) else float("nan")
    area = float(area) if pd.notna(area) else float("nan")
    show_average = bool(qty and area and qty > 0)
    average = number((area * 1000) / (qty * 1000)) if show_average else None
    references = []
    if show_average and not data["apartments"].empty:
        apartments = data["apartments"]
        for key in ("msk", "rf"):
            reference = apartments[apartments["region_key"].eq(key) & apartments["тип"].eq("Все квартиры")]
            if not reference.empty:
                item = reference.iloc[0]
                reference_qty = pd.to_numeric(item["количество_шт_num"], errors="coerce")
                reference_area = pd.to_numeric(item[AREA], errors="coerce")
                if pd.notna(reference_qty) and reference_qty > 0 and pd.notna(reference_area):
                    references.append({"region": key, "averageAreaM2": number(reference_area * 1000 / reference_qty)})
    base = developers[AREA].sum()
    share = number(row[AREA] / base * 100) if base != 0 else None
    comparison = developers.head(10).copy()
    if developer not in comparison[NAME].values:
        comparison = pd.concat([comparison, selected], ignore_index=True)
    return {
        "region": region, "developer": table_row(row),
        "summary": {"countThousand": number(row[QUANTITY]), "areaThousandM2": number(row[AREA]),
                    "averageAreaM2": average, "marketSharePercent": share,
                    "marketBaseAreaThousandM2": number(base), "place": int(row["place"]),
                    "totalDevelopers": len(developers)},
        "referenceAverages": references, "rooms": rooms(row),
        "comparison": [table_row(item) for _, item in comparison.iterrows()],
    }
