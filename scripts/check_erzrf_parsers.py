"""Fast checks for ERZRF parser outputs."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.parsers import erzrf_cards, erzrf_top  # noqa: E402
from pipeline.parsers.common import DATA_COLUMNS  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)

        top_path = base / "top_obyem_stroitelstva_rf_20260702.xlsx"
        pd.DataFrame([
            {
                "Место": 1,
                "Наименование, регион": "ГК Тест, г.Москва",
                "Строится, м²": "1 234",
                "С переносом срока, м²": 100,
                "%": 8.1,
                "Рейтинг ЕРЗ": 5.0,
            }
        ]).to_excel(top_path, index=False)
        top = erzrf_top.parse(top_path)
        _require(not top.empty, "ERZRF top parser returned no rows")
        _require(set(DATA_COLUMNS).issubset(top.columns), "ERZRF top parser missed standard columns")
        _require(top.loc[0, "indicator_id"] == "realty_erzrf_top_rf", "ERZRF top indicator_id mismatch")
        _require(top.loc[0, "sorting"] == "obyem_stroitelstva", "ERZRF top sorting was not parsed")
        _require(top.loc[0, "year"] == 2026, "ERZRF top year was not parsed")
        _require(float(top.loc[0, "value"]) == 1234.0, "ERZRF top value was not parsed")

        cards_path = base / "cards_20260702.xlsx"
        with pd.ExcelWriter(cards_path, engine="openpyxl") as writer:
            pd.DataFrame([
                {
                    "name_card": "ГК Тест",
                    "name_table": "Тест",
                    "slug": "brand/test",
                    "url": "https://example.test",
                    "regions_count": 1,
                    "regions_as_of": "01.07.2026",
                    "Строится_м²": "2 000",
                    "Строится_перенос_%": 12.5,
                    "Сдано_2025_м²": "1 500",
                    "Перенос_2025_%": 3.5,
                    "Уточн_2025_мес": 1.2,
                }
            ]).to_excel(writer, sheet_name="cards", index=False)
        cards = erzrf_cards.parse(cards_path)
        _require(not cards.empty, "ERZRF cards parser returned no rows")
        _require(set(DATA_COLUMNS).issubset(cards.columns), "ERZRF cards parser missed standard columns")
        _require(set(cards["view"]) >= {"construction_area_m2", "construction_transfer_pct", "commissioned_m2", "transfer_pct", "delay_months"}, "ERZRF cards metrics missing")
        commissioned = cards[cards["view"] == "commissioned_m2"].iloc[0]
        _require(int(commissioned["year"]) == 2025, "ERZRF cards year metric was not parsed")
        _require(float(commissioned["value"]) == 1500.0, "ERZRF cards numeric value was not parsed")

    print("erzrf parser checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
