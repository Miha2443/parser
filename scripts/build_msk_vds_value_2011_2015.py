"""Разовый расчёт ВДС Москвы по отраслям в рублях за 2011-2015.

В исходниках за 2011-2015 у Москвы есть только доли ВДС (% к итогу,
старый ОКВЭД-2007) и сводный ВРП в рублях. Рублёвой разбивки по отраслям
нет. Восстанавливаем её точно:

    значение_отрасли = ВРП_Москвы(год) × доля_отрасли(год) / 100

Корректность подтверждается 2016 годом: сумма vds_value Москвы за 2016
ровно равна ВРП Москвы 2016, т.е. ВДС по отраслям в сумме = ВРП.

Результат пишем в committed-файл data/derived/vds_msk_value_2011_2015.csv
(его подхватывает app/data_access.load_national_accounts). Это разовая
операция: данные за 2011-2015 исторические и не меняются. Перезапускать
скрипт нужно только если Росстат пересмотрит ВРП Москвы за эти годы.

Запуск (после сборки витрин ETL):
    py scripts/build_msk_vds_value_2011_2015.py
"""
from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd

from pipeline.parsers.rosstat_common import NA_COLUMNS

OUT = ROOT / "data" / "derived" / "vds_msk_value_2011_2015.csv"
YEARS = range(2011, 2016)
INDICATOR_ID = "vds_msk_value_legacy"
INDICATOR_TITLE = "ВДС Москвы по отраслям в рублях (расчёт: ВРП × доля), ОКВЭД-2007"


def main() -> int:
    vrp = pd.read_pickle(ROOT / "data" / "processed" / "vrp_msk.pkl")
    shares = pd.read_pickle(ROOT / "data" / "processed" / "vds_msk_legacy.pkl")

    totals = (
        vrp[(vrp["metric"] == "vrp_total") & (vrp["region"] == "Москва")]
        .set_index("year")["value"]
    )  # млн руб
    sh = shares[(shares["metric"] == "vds_structure") & (shares["region"] == "Москва")]

    rows: list[dict] = []
    now = datetime.now()
    for _, r in sh.iterrows():
        year = int(r["year"])
        if year not in YEARS or year not in totals.index:
            continue
        value = float(totals.loc[year]) * float(r["value"]) / 100.0  # млн руб
        rows.append({
            "section": "national_accounts",
            "indicator_id": INDICATOR_ID,
            "indicator_title": INDICATOR_TITLE,
            "view": r["view"],
            "region": "Москва",
            "year": year,
            "month": None,
            "quarter": None,
            "period_type": "year",
            "metric": "vds_value",
            "value": round(value, 3),
            "unit": "млн руб",
            "source_file": "расчёт: ВРП Москвы × доля ВДС (ОКВЭД-2007)",
            "loaded_at": now,
        })

    out_df = pd.DataFrame(rows)[NA_COLUMNS].sort_values(["year", "view"]).reset_index(drop=True)
    OUT.parent.mkdir(parents=True, exist_ok=True)
    out_df.to_csv(OUT, index=False, encoding="utf-8")

    # Сверка: сумма по году должна совпасть с ВРП Москвы.
    check = out_df.groupby("year")["value"].sum()
    print(f"Записано {len(out_df)} строк в {OUT.relative_to(ROOT)}")
    for year in YEARS:
        if year in totals.index:
            diff = check.get(year, 0) - float(totals.loc[year])
            print(f"  {year}: сумма={check.get(year, 0):,.0f} млн руб  ВРП={float(totals.loc[year]):,.0f}  Δ={diff:,.1f}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
