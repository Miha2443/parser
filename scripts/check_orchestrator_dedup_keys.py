"""Fast checks for orchestrator processed-frame deduplication keys."""
from __future__ import annotations

import sys
from pathlib import Path

import pandas as pd


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from pipeline import orchestrator  # noqa: E402


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def main() -> int:
    df = pd.DataFrame([
        {
            "indicator_id": "realty_kvartirografia",
            "view": "area_thousand_m2",
            "region": "Россия",
            "year": 2026,
            "month": 0,
            "period_type": "snapshot",
            "unit": "тыс. м²",
            "entity_type": "developer",
            "entity_name": "А",
            "metric_column": "площадь_тыс_м²",
            "source_file": "kvartirografia_20260702.xlsx",
            "value": 1.0,
        },
        {
            "indicator_id": "realty_kvartirografia",
            "view": "area_thousand_m2",
            "region": "Россия",
            "year": 2026,
            "month": 0,
            "period_type": "snapshot",
            "unit": "тыс. м²",
            "entity_type": "developer",
            "entity_name": "Б",
            "metric_column": "площадь_тыс_м²",
            "source_file": "kvartirografia_20260702.xlsx",
            "value": 2.0,
        },
        {
            "indicator_id": "realty_kvartirografia",
            "view": "area_thousand_m2",
            "region": "Россия",
            "year": 2026,
            "month": 0,
            "period_type": "snapshot",
            "unit": "тыс. м²",
            "entity_type": "developer",
            "entity_name": "А",
            "metric_column": "площадь_тыс_м²",
            "source_file": "kvartirografia_20260702.xlsx",
            "value": 3.0,
        },
    ])

    old_keys = [
        c for c in ["indicator_id", "view", "region", "year", "month", "period_type", "metric"]
        if c in df.columns
    ]
    old = df.drop_duplicates(subset=old_keys, keep="last")
    _require(len(old) == 1, "fixture should reproduce the old over-deduplication")

    new = orchestrator._deduplicate_processed(df)
    _require(len(new) == 2, "dedup should preserve distinct entity rows")
    values = dict(zip(new["entity_name"], new["value"]))
    _require(values == {"А": 3.0, "Б": 2.0}, "dedup should keep last exact entity duplicate only")

    print("orchestrator dedup key checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
