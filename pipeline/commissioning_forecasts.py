"""Literal commissioning forecasts from the monitoring workbook, never facts."""
from collections import Counter
from datetime import datetime
import math
import re


FORECAST_FIELDS = {
    13: ("housing_living", "РВ жилье, жилая площадь"),
    14: ("housing_total", "РВ жилье, общая площадь"),
    15: ("renovation_living", "РВ жилье Реновация, жилая площадь"),
    16: ("nonres_standalone", "РВ нежилье отдельное, общая площадь"),
    17: ("nonres_embedded", "РВ нежилье в жилье, общая площадь"),
    18: ("nonres_total", "РВ нежилье всего, общая площадь"),
    19: ("offices", "РВ административно-деловые"),
    20: ("industry", "РВ промышленность"),
    21: ("social", "РВ социальные"),
    22: ("trade", "РВ торговля"),
    23: ("hotels", "РВ гостиницы"),
    24: ("transport", "РВ транспорт"),
    25: ("warehouses", "РВ склады"),
    26: ("other", "РВ прочее"),
    38: ("metro_stations", "станции метро, ед."),
    39: ("metro_tunnels", "тоннели метро,км"),
    40: ("roads", "дороги, км"),
    41: ("bridges", "мосты, тоннели, эстакады, ед."),
    42: ("depots", "электродепо, ед."),
    43: ("crossings", "пешеходные переходы, ед."),
}


def _normalized(value):
    return re.sub(r"\s+", "", str(value or "")).casefold().replace("ё", "е").replace("траспорт", "транспорт")


def _number(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        result = float(re.sub(r"\s+", "", value).replace(",", ".") if isinstance(value, str) else value)
    except (ValueError, TypeError, OverflowError):
        return None
    return result if math.isfinite(result) else None


def empty_forecasts(source_file=None, source_date=None):
    return {"source_file": source_file, "source_date": source_date, "rows": [],
            "unitBasis": "assumed_thousand_m2", "notes": [
                "Прогнозы площадей в строках РВ приняты в тыс. м² по масштабу исходного ряда; "
                "отдельная единица площади на листе не указана. Линейные показатели используют единицы подписей строк."]}


def read_commissioning_forecasts(book, source_file=None, source_date=None):
    result = empty_forecasts(source_file, source_date)
    if "Прогнозы" not in book.sheetnames:
        result["notes"].append("Лист «Прогнозы» отсутствует; прогнозные значения не подставляются.")
        return result
    cells = list(book["Прогнозы"].iter_rows(min_row=1, max_row=43, max_col=128))
    headers = [_number(cell.value) for cell in cells[1][3:]] if len(cells) > 1 else []
    valid_years = [int(value) if value is not None and value == int(value) and 1900 <= value <= 2200 else None
                   for value in headers]
    duplicates = {year for year, count in Counter(valid_years).items() if year is not None and count > 1}
    fields = {}
    for row, (key, label) in FORECAST_FIELDS.items():
        if len(cells) >= row and _normalized(cells[row - 1][2].value) == _normalized(label):
            fields[row] = key
        else:
            result["notes"].append(f"Прогнозы!C{row}: подпись показателя не подтверждена; {key} недоступен.")
    for offset, year in enumerate(valid_years, 3):
        if year is None or year in duplicates:
            continue
        item = {key: None for key, _ in FORECAST_FIELDS.values()}
        item["year"] = year
        for row, key in fields.items():
            item[key] = _number(cells[row - 1][offset].value)
        if any(value is not None for key, value in item.items() if key != "year"):
            result["rows"].append(item)
    if duplicates:
        result["notes"].append("Повторяющиеся годы прогноза пропущены: " + ", ".join(map(str, sorted(duplicates))))
    result["rows"].sort(key=lambda row: row["year"])
    return result


def filename_date(path):
    match = re.search(r"_(\d{8})$", path.stem)
    try:
        return datetime.strptime(match[1], "%Y%m%d").date().isoformat() if match else None
    except ValueError:
        return None
