"""Contract checks for the current-construction Nashdom source."""
from __future__ import annotations

import json
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import nashdom_checker as nc  # noqa: E402
from pipeline.parsers.nashdom_construction_operational import parse  # noqa: E402
from pipeline.data_access import _parse_rasprod_number  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    html = """
    <html><body>
      <div>Отчетный период Август 2026. Дата публикации 30 сентября 2026 года</div>
      <div><div>Всего жилой площади</div><span class="IndicatorValue-ab">15 743 тыс. м²</span></div>
      <div><div>Продажи открыты</div><span class="IndicatorValue-ab">14 140 тыс. м²</span></div>
      <div><div>Проданная площадь</div><span class="Value-sc-18spgfx-a">7 366 тыс. м²</span><span class="Value-sc-1x9zma-a">47%</span></div>
      <div><div>Непроданная площадь</div><span class="Value-sc-18spgfx-a">6 774 тыс. м²</span><span class="Value-sc-1x9zma-a">43%</span></div>
      <div><div>Продажи не открыты</div><span class="Value-sc-18spgfx-a">1 603 тыс. м²</span><span class="Value-sc-1x9zma-a">10%</span></div>
      <div><div>Стоимость 1 квадратного метра</div><span class="Value-sc-182uuls-a">441 306 руб.</span></div>
      <div><div>Объем привлеченных средств</div><span class="Value-sc-182uuls-a">3 250 869 млн руб.</span></div>
    </body></html>
    """
    sale = nc._parse_apartment_sales_html(html, "Город Москва")
    _require(sale["report_period"] == "Август 2026", "report period was not parsed")
    _require(sale["report_date"] == "30.09.2026", "publication date was not parsed")
    _require(sale["metrics"]["sold_pct"] == "47%", "sold share was not parsed")
    _require("7 366" in sale["metrics"]["sold_thousand_m2"], "sold area was not parsed")
    _require(_parse_rasprod_number("441 306 руб.") == 441306,
             "DataAccess should parse the price unit")

    payload = {
        "construction": [
            {"region_key": region, "report_period": "Сентябрь 2026",
             "area_kind": kind, "area_thousand_m2": value}
            for region, kind, value in (
                ("rf", "living", "121 876"), ("rf", "total", "205 560"),
                ("msk", "living", "15 640"), ("msk", "total", "26 964"),
            )
        ],
        "sales": [
            {"region_key": region, "report_period": sale["report_period"],
             "metrics": sale["metrics"]}
            for region in ("rf", "msk")
        ],
    }
    nc._validate_construction_operational_result(payload)
    broken = json.loads(json.dumps(payload))
    del broken["sales"][0]["metrics"]["sales_open_thousand_m2"]
    try:
        nc._validate_construction_operational_result(broken)
    except ValueError:
        pass
    else:
        raise AssertionError("incomplete apartment sales snapshot should be rejected")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "construction_operational_20261001.json"
        source.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        frame = parse(source)
    _require(len(frame) == 24, f"unexpected normalized row count: {len(frame)}")
    living = frame[(frame["region"].eq("Москва")) & (frame["view"].eq("living_area"))]
    _require(len(living) == 1 and living.iloc[0]["value"] == 15640,
             "Moscow living area normalization is wrong")
    _require(set(frame["year"]) == {2026}, "year normalization is wrong")
    _require(set(frame["month"]) == {8, 9}, "month normalization is wrong")

    print("nashdom construction operational checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
