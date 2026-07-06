"""Fast checks for Nashdom kvartirografia parser output."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.parsers import nashdom_kvartirografia as parser  # noqa: E402
from pipeline.parsers.common import DATA_COLUMNS  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kvartirografia_20260702.xlsx"
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            pd.DataFrame([
                {
                    "region_key": "rf",
                    "region": "Российская Федерация",
                    "report_date": "02.07.2026",
                    "тип": "Все квартиры",
                    "количество_шт": "2 430 266",
                    "площадь_тыс_м²": "119 532",
                }
            ]).to_excel(writer, sheet_name="apartments", index=False)
            pd.DataFrame([
                {
                    "region_key": "rf",
                    "region": "Российская Федерация",
                    "report_date": "02.07.2026",
                    "диапазон": "0-25 м²",
                    "доля": "6%",
                }
            ]).to_excel(writer, sheet_name="distribution", index=False)
            pd.DataFrame([
                {
                    "region_key": "rf",
                    "region": "Российская Федерация",
                    "report_date": "02.07.2026",
                    "наименование": "Самолет",
                    "квартиры_тыс_шт": "105,3",
                    "площадь_тыс_м²": "4 538",
                    "доля_1комн_%": "47",
                    "доля_2комн_%": "36",
                    "доля_3комн_%": "14",
                    "доля_4+комн_%": "3",
                }
            ]).to_excel(writer, sheet_name="developers", index=False)
            pd.DataFrame([
                {
                    "region_key": "rf",
                    "region": "Российская Федерация",
                    "report_date": "02.07.2026",
                    "наименование": "Город Москва",
                    "квартиры_тыс_шт": "290,2",
                    "площадь_тыс_м²": "15 960",
                    "доля_1комн_%": "41",
                    "доля_2комн_%": "37",
                    "доля_3комн_%": "18",
                    "доля_4+комн_%": "4",
                }
            ]).to_excel(writer, sheet_name="regions", index=False)

        df = parser.parse(path)
        _require(not df.empty, "kvartirografia parser returned no rows")
        _require(set(DATA_COLUMNS).issubset(df.columns), "kvartirografia parser missed standard columns")
        _require(set(df["entity_type"]) == {"apartment_type", "area_range", "developer", "region_rank"}, "entity types missing")
        _require(set(df["view"]) >= {"apartments_count", "area_thousand_m2", "share_pct", "one_room_share_pct"}, "metrics missing")

        apartments = df[(df["entity_type"] == "apartment_type") & (df["view"] == "apartments_count")].iloc[0]
        _require(float(apartments["value"]) == 2430266.0, "apartment count was not parsed")
        _require(int(apartments["year"]) == 2026, "report year was not parsed")

        share = df[(df["entity_type"] == "area_range") & (df["view"] == "share_pct")].iloc[0]
        _require(float(share["value"]) == 6.0, "share percent was not parsed")

        developer = df[(df["entity_type"] == "developer") & (df["view"] == "apartments_thousand_count")].iloc[0]
        _require(float(developer["value"]) == 105.3, "developer numeric value was not parsed")

    print("nashdom kvartirografia parser checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
