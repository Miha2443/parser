"""Fast checks for Nashdom rasprodannost parser output."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path
from urllib.parse import parse_qs, urlsplit


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline.parsers import nashdom_rasprodannost as parser  # noqa: E402
from pipeline.parsers.common import DATA_COLUMNS  # noqa: E402
from nashdom_checker import _rasprod_region_url  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    base_url = "https://example.test/report?repYear=2026&repMonth=8&foCd=all&regionCd=all"
    msk_url = _rasprod_region_url(base_url, "Город Москва")
    msk_query = parse_qs(urlsplit(msk_url).query)
    _require(msk_query.get("regionCd") == ["77"],
             "Moscow URL should use the site's regionCd=77")
    _require("foCd" not in msk_query and msk_query.get("repMonth") == ["8"],
             "Moscow URL should preserve period and remove the all-Russia district")
    rf_url = _rasprod_region_url(msk_url, "Все")
    rf_query = parse_qs(urlsplit(rf_url).query)
    _require(rf_query.get("regionCd") == ["all"] and rf_query.get("foCd") == ["all"],
             "Russia URL should restore all-region filters")

    payload = [
        {
            "report_date": "11.06.2026",
            "report_period": "Май 2026",
            "region": "",
            "region_key": "rf",
            "year": 2026,
            "month_num": 5,
            "source": "rasprodannost",
            "url": "https://example.test",
            "kpi": [
                {
                    "название": "Объем жилищного строительства",
                    "значение": "119 814",
                    "единица": "тыс. м²",
                    "по_годам": {"2026": "35 171", "2031+": "4 616"},
                },
                {
                    "название": "Распроданность",
                    "значение": "31%",
                    "единица": "36 665 тыс. м²",
                    "по_годам": {"2026": "53%"},
                },
            ],
            "tables": {
                "Девелоперы": [
                    {
                        "наименование": "Самолет",
                        "Объем жил. строительства": "4 517 898",
                        "Распроданность": "37%",
                        "Стройготовность": "49%",
                        "Отношение распроданности  к стройготовности": "75%",
                    }
                ]
            },
        }
    ]
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "rasprodannost_20260618.json"
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        df = parser.parse(path)

    _require(not df.empty, "rasprodannost parser returned no rows")
    _require(set(DATA_COLUMNS).issubset(df.columns), "rasprodannost parser missed standard columns")
    _require(set(df["entity_type"]) >= {"kpi_total", "kpi_by_delivery_year", "Девелоперы"}, "entity types missing")
    _require(set(df["view"]) >= {"construction_volume_thousand_m2", "construction_volume_m2", "sold_pct", "readiness_pct", "sold_to_readiness_pct"}, "metrics missing")

    total = df[(df["entity_type"] == "kpi_total") & (df["view"] == "construction_volume_thousand_m2")].iloc[0]
    _require(float(total["value"]) == 119814.0, "KPI total value was not parsed")
    _require(total["region"] == "Россия", "region_key should map to Russia")
    _require(int(total["year"]) == 2026 and int(total["month"]) == 5, "period fields were not parsed")

    forecast = df[(df["entity_type"] == "kpi_by_delivery_year") & (df["delivery_year_label"] == "2031+")].iloc[0]
    _require(int(forecast["delivery_year"]) == 2031, "forecast delivery year was not parsed")
    _require(float(forecast["value"]) == 4616.0, "forecast value was not parsed")

    developer = df[(df["entity_type"] == "Девелоперы") & (df["view"] == "sold_to_readiness_pct")].iloc[0]
    _require(developer["entity_name"] == "Самолет", "table entity name was not parsed")
    _require(float(developer["value"]) == 75.0, "table percent value was not parsed")

    print("nashdom rasprodannost parser checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
