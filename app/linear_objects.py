"""План и факт ввода линейных объектов из отчёта ДСТИ."""
from __future__ import annotations

from datetime import date, datetime
from pathlib import Path
import json
import math
import re

import openpyxl
import pandas as pd


SOURCE_DIR = Path(__file__).resolve().parents[1] / "data" / "raw" / "realty" / "linear_objects"
INDICATORS = ("1.1", "1.2", "1.3", "1.4")
DSTI_SHEET = "ДСТИ"
DSTI_LABELS = {
    "протяженность дорог": ("1.1", "км"),
    "кол-во искусственных сооружений": ("1.2", "ед"),
    "кол-во внеуличных пеш.переходов": ("1.3", "ед"),
    "кол-во очистных сооружений": ("1.4", "ед"),
}
PERIOD_HEADER = re.compile(r"([1-4])\s*квартал\s*(20\d{2}).*?\((факт|план)\)", re.IGNORECASE | re.DOTALL)


def _source_date(path: Path) -> date:
    name = path.stem
    iso = re.search(r"(20\d{2})[-_.](\d{1,2})[-_.](\d{1,2})", name)
    local = re.search(r"(\d{1,2})[.\-_](\d{1,2})[.\-_](20\d{2})", name)
    try:
        if iso:
            return date(*(int(part) for part in iso.groups()))
        if local:
            day, month, year = map(int, local.groups())
            return date(year, month, day)
    except ValueError:
        pass
    return datetime.fromtimestamp(path.stat().st_mtime).date()


def latest_source(directory: Path = SOURCE_DIR) -> Path | None:
    files = [path for suffix in ("*.xlsx", "*.json") for path in directory.glob(suffix)
             if not path.name.startswith("~$")]
    return max(files, key=lambda path: (_source_date(path), path.suffix.lower() == ".xlsx", path.stat().st_mtime)) if files else None


def source_date(path: Path) -> date:
    return _source_date(path)


def _number(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(value) and value >= 0 else None
    if isinstance(value, str):
        cleaned = value.strip().replace("\u00a0", "").replace(" ", "").replace(",", ".")
        if not cleaned or cleaned == "*":
            return None
        try:
            result = float(cleaned)
            return result if math.isfinite(result) and result >= 0 else None
        except ValueError:
            return None
    return None


def _normalized(value: object) -> str:
    return " ".join(str(value or "").casefold().replace("ё", "е").split())


def read_linear_objects(path: Path) -> pd.DataFrame:
    """Читает строки 1.1–1.4 и квартальные столбцы по названиям, без привязки к буквам Excel."""
    if path.suffix.lower() == ".json":
        records = json.loads(path.read_text(encoding="utf-8"))
        data = pd.DataFrame(records)
        required = {"code", "indicator", "unit", "year", "quarter", "plan", "fact"}
        if not required.issubset(data.columns) or set(data["code"]) != set(INDICATORS):
            raise ValueError("В сохранённом срезе нет полного набора показателей 1.1–1.4.")
        if data.duplicated(["code", "year", "quarter"]).any():
            raise ValueError("В сохранённом срезе повторяются показатель и квартал.")
        return data.sort_values(["code", "year", "quarter"]).reset_index(drop=True)
    workbook = openpyxl.load_workbook(path, read_only=True, data_only=True)
    try:
        monitoring = DSTI_SHEET in workbook.sheetnames
        sheet = workbook[DSTI_SHEET] if monitoring else workbook.active
        return read_linear_sheet(sheet, monitoring=monitoring)
    finally:
        workbook.close()


def read_linear_sheet(sheet, *, monitoring: bool = False) -> pd.DataFrame:
    """Read a bounded quarterly table, including workbooks without dimensions.

    Monitoring's 1.1-1.4 codes can be Excel dates. Match its named indicators,
    not those date values, and never read financial/permit rows as commissioning.
    """
    rows = list(sheet.iter_rows(max_row=200, max_col=128, values_only=True))
    header_index = next(
        (i for i, row in enumerate(rows[:15]) if any(_normalized(cell) == "показатель" for cell in row)),
        None,
    )
    if header_index is None:
        raise ValueError("В отчёте не найдена строка заголовков с колонкой «Показатель».")
    header = rows[header_index]
    label_col = next(i for i, value in enumerate(header) if _normalized(value) == "показатель")
    unit_cols = [i for i, value in enumerate(header) if _normalized(value) == "единица измерения"]
    unit_col = unit_cols[0] if len(unit_cols) == 1 else label_col + 1
    period_cols_header = [i for i, value in enumerate(header) if _normalized(value) == "период расчета"]
    if monitoring and (len(unit_cols) != 1 or len(period_cols_header) != 1):
        raise ValueError("ДСТИ: не подтверждены колонки единиц измерения и периода расчета.")
    period_cols: dict[tuple[int, int, str], int] = {}
    for col, value in enumerate(rows[header_index]):
        match = PERIOD_HEADER.search(str(value or ""))
        if match:
            quarter, year, kind = match.groups()
            key = int(year), int(quarter), kind.casefold()
            if key in period_cols:
                raise ValueError(f"Повторён столбец {key} в отчёте.")
            period_cols[key] = col
    if not period_cols:
        raise ValueError("В отчёте не найдены квартальные столбцы плана и факта.")
    periods = sorted({(year, quarter) for year, quarter, _ in period_cols})
    result = []
    seen: set[str] = set()
    for row_index, row in enumerate(rows[header_index + 1:], header_index + 1):
        label = str(row[label_col] or "").strip()
        unit = str(row[unit_col] or "").strip()
        if monitoring:
            matched = DSTI_LABELS.get(_normalized(label))
            if matched is None:
                continue
            code, expected_unit = matched
            if _normalized(unit).rstrip(".") != expected_unit:
                raise ValueError(f"ДСТИ: несовместимая единица измерения {code}.")
            if _normalized(row[period_cols_header[0]]) != "за текущий период":
                raise ValueError(f"ДСТИ: квартальные значения {code} не подтверждены как аддитивные.")
        else:
            code = str(row[0] or "").strip().rstrip(".")
            if code not in INDICATORS:
                continue
        if code in seen:
            raise ValueError(f"Показатель {code} повторяется в отчёте.")
        seen.add(code)
        if not label or not unit:
            raise ValueError(f"Нет названия или единицы измерения для показателя {code}.")
        for year, quarter in periods:
            fact_col = period_cols.get((year, quarter, "факт"))
            plan_col = period_cols.get((year, quarter, "план"))
            result.append({
                "code": code, "indicator": label, "unit": unit,
                "year": year, "quarter": quarter,
                "plan": _number(row[plan_col]) if plan_col is not None else None,
                "fact": _number(row[fact_col]) if fact_col is not None else None,
                "plan_cell": f"{openpyxl.utils.get_column_letter(plan_col + 1)}{row_index + 1}" if plan_col is not None else None,
                "fact_cell": f"{openpyxl.utils.get_column_letter(fact_col + 1)}{row_index + 1}" if fact_col is not None else None,
            })
    missing = set(INDICATORS) - seen
    if missing and not monitoring:
        raise ValueError("В отчёте отсутствуют показатели: " + ", ".join(sorted(missing)))
    if not result:
        raise ValueError("ДСТИ: строки линейных показателей не найдены.")
    return pd.DataFrame(result).sort_values(["code", "year", "quarter"]).reset_index(drop=True)


def period_summary(data: pd.DataFrame, year: int, quarter: int, cumulative: bool) -> pd.DataFrame:
    """Суммирует кварталы только при наличии всех квартальных значений."""
    result = []
    required = set(range(1, quarter + 1)) if cumulative else {quarter}
    for code, group in data[data["year"].eq(year)].groupby("code", sort=True):
        selected = group[group["quarter"].isin(required)]
        item = selected.iloc[0] if not selected.empty else group.iloc[0]

        def total(column: str) -> float | None:
            if set(selected["quarter"]) != required or selected[column].isna().any():
                return None
            return float(selected[column].sum())

        plan, fact = total("plan"), total("fact")
        result.append({
            "code": code, "indicator": item["indicator"], "unit": item["unit"],
            "year": year, "quarter": quarter, "plan": plan, "fact": fact,
            "percent": fact / plan * 100 if fact is not None and plan is not None and plan > 0 else None,
        })
    return pd.DataFrame(result).sort_values("code").reset_index(drop=True)
