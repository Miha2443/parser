"""Fast checks for Nashdom monitoring 2.0 parser output."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.parsers import nashdom_monitoring_2_0 as parser  # noqa: E402
from pipeline.parsers.common import DATA_COLUMNS  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "monitoring_2_0_20260702.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame([
                {
                    "УИН": "RV-1",
                    "Год ввода по Мосстату": 2026,
                    "Дата ввода по Мосстату": "2026-07-01",
                    "Округ": "ЦАО",
                    "Район": "Тверской",
                    "Группировка": "Жилье",
                    "Источник финансирования": "Внебюджет",
                    "Застройщик": 'ООО "Тест"',
                    "Группа компаний": "ТЕСТ",
                    "Общая площадь": "1 234,5",
                    "Жилая площадь": 1000,
                    "Количество квартир": 10,
                    "Машиномест": 20,
                }
            ]).to_excel(writer, sheet_name="Реестр РВ", index=False)
            pd.DataFrame([
                {
                    "УИН": "OKS-1",
                    "Назначение": "Нежилье",
                    "Год окончания для Минстроя": 2027,
                    "Округ": "САО",
                    "Район": "Беговой",
                    "Застройщик": 'ООО "ОКС"',
                    "Группа компаний": "ОКС",
                    "Общая площадь": 500,
                    "Места в ДОУ": 100,
                    "Количество апартаментов": 30,
                }
            ]).to_excel(writer, sheet_name="Реестр ОКС", index=False)

        df = parser.parse(path)
        _require(not df.empty, "monitoring parser returned no rows")
        _require(set(DATA_COLUMNS).issubset(df.columns), "monitoring parser missed standard columns")
        _require(set(df["registry"]) == {"rv", "oks"}, "monitoring parser missed registry sheets")
        _require(set(df["view"]) >= {"total_area_m2", "living_area_m2", "apartments_count", "parking_places", "kindergarten_places"}, "monitoring metrics missing")

        total = df[(df["uin"] == "RV-1") & (df["view"] == "total_area_m2")].iloc[0]
        _require(float(total["value"]) == 1234.5, "monitoring numeric value was not parsed")
        _require(int(total["year"]) == 2026, "monitoring RV year was not parsed")
        _require(total["source_date"] == "2026-07-02", "monitoring source date was not parsed")

        oks = df[(df["uin"] == "OKS-1") & (df["view"] == "kindergarten_places")].iloc[0]
        _require(int(oks["year"]) == 2027, "monitoring OKS year was not parsed")

    print("nashdom monitoring parser checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
