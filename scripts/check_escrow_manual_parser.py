"""Fast checks for manual escrow parser output."""
from __future__ import annotations

import sys
import tempfile
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.parsers import escrow_manual as parser  # noqa: E402
from pipeline.parsers.common import DATA_COLUMNS  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "escrow.xlsx"
        columns = [
            "ID объекта",
            "Наименование проекта",
            "Адрес объекта",
            "ИНН застройщика",
            "Наименование застройщика",
            "ГК застройщика",
            "Жилая площадь, м2",
            "Банк-кредитор",
            "Сумма кредита (займа) в соответствии с условиями договора, руб.",
            "Сумма задолженности по договору кредита (займа), руб.",
            "Проект с использованием эскроу-счетов (да/нет)",
            "Продажи открыты (да/нет)",
            "Выручка от реализации всех площадей, руб.",
            "Выручка от реализации всех площадей по эскроу, руб.",
            "Уровень покрытия (%)",
            "Плановый срок ввода (текущий)",
            "Накопленный срок переноса ввода объекта (мес.)",
            "Распроданность жилой площади (%)",
        ]
        raw = pd.DataFrame(
            [
                ["service title"] + [""] * (len(columns) - 1),
                columns,
                [
                    123,
                    "ЖК Тест",
                    "Москва",
                    "7700000000",
                    "СЗ Тест",
                    "Тест ГК",
                    "10 000",
                    "Банк",
                    "1 000 000",
                    "500 000",
                    "да",
                    "да",
                    "2 500 000",
                    "1 500 000",
                    "60%",
                    "2026-09-30",
                    12,
                    0.75,
                ],
            ]
        )
        with pd.ExcelWriter(path, engine="openpyxl") as writer:
            raw.to_excel(writer, sheet_name="Выгрузка", index=False, header=False)

        df = parser.parse(path)

    _require(not df.empty, "escrow parser returned no rows")
    _require(set(DATA_COLUMNS).issubset(df.columns), "escrow parser missed standard columns")
    _require(set(df["view"]) >= {"living_area_m2", "credit_amount_rub", "credit_debt_rub", "revenue_total_rub", "coverage_pct", "sold_living_area_pct"}, "escrow metrics missing")

    living = df[df["view"] == "living_area_m2"].iloc[0]
    _require(float(living["value"]) == 10000.0, "living area was not parsed")
    _require(int(living["year"]) == 2026 and int(living["month"]) == 9, "planned delivery date was not parsed")
    _require(living["object_id"] == 123, "object id was not preserved")

    coverage = df[df["view"] == "coverage_pct"].iloc[0]
    _require(float(coverage["value"]) == 60.0, "percent value was not parsed")

    print("escrow manual parser checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
