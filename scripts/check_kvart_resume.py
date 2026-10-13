"""Fast checks for kvartirografia resume helpers."""
from __future__ import annotations

import json
import os
import sys
import tempfile
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from nashdom_checker import (  # noqa: E402
    _initial_kvart_data,
    _kvart_resume_per_dev,
    _kvart_count_matches,
    _load_kvart_resume,
    _upsert_region_snapshot,
)


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise AssertionError(message)


def test_load_kvart_resume() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kvartirografia_20260706.json"
        path.write_text(
            json.dumps([{"region_key": "rf"}, "bad", {"region_key": "msk"}]),
            encoding="utf-8",
        )

        rows = _load_kvart_resume(path)
        _require(
            [row["region_key"] for row in rows] == ["rf", "msk"],
            "checkpoint loader should keep only dict rows",
        )
        _require(
            _load_kvart_resume(Path(tmp) / "missing.json") == [],
            "missing checkpoint should be empty",
        )


def test_force_ignores_kvart_resume() -> None:
    previous = os.environ.get("NASHDOM_FORCE")
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "kvartirografia_20260706.json"
        path.write_text(json.dumps([{"region_key": "rf"}]), encoding="utf-8")
        try:
            os.environ.pop("NASHDOM_FORCE", None)
            _require(_initial_kvart_data(path), "normal mode should load checkpoint")
            os.environ["NASHDOM_FORCE"] = "1"
            _require(_initial_kvart_data(path) == [], "force mode should ignore checkpoint")
        finally:
            if previous is None:
                os.environ.pop("NASHDOM_FORCE", None)
            else:
                os.environ["NASHDOM_FORCE"] = previous


def test_upsert_region_snapshot() -> None:
    rows = [
        {"region_key": "rf", "value": 1},
        {"region_key": "msk", "value": 2},
    ]
    _upsert_region_snapshot(rows, {"region_key": "rf", "value": 3})
    _require(len(rows) == 2, "upsert should not duplicate region rows")
    by_region = {row["region_key"]: row["value"] for row in rows}
    _require(by_region == {"rf": 3, "msk": 2}, "upsert should replace only target region")


def test_resume_per_dev() -> None:
    existing = {
        "apartments_per_dev": [
            {"site_name": "A", "apartments": {"all": {"count": "10", "area": "1"}}},
            {"site_name": "B", "apartments": {"all": {"count": "20", "area": "1"}}},
            {"site_name": "C", "apartments": {"all": {"count": "30", "area": "1"}}},
        ],
        "per_dev_attempts": [
            {"site_name": "A", "status": "ok"},
            {"site_name": "B", "status": "switch_fail"},
            {"site_name": "C", "status": "rejected"},
        ],
    }

    per_dev, attempts, done = _kvart_resume_per_dev(existing)
    _require(done == {"A"}, "rejected attempts must remain retryable")
    _require([row["site_name"] for row in per_dev] == ["A"], "per-dev rows should match valid successful attempts")
    _require(len(attempts) == 3, "retain diagnostic history")
    existing["per_dev_attempts"].append({"site_name": "missing", "status": "ok"})
    _require("missing" not in _kvart_resume_per_dev(existing)[2], "absent payload cannot be done")
    existing.update(report_date="01.01.2026", developers=[{"наименование": "A", "квартиры_тыс_шт": "0.01"}])
    _require(_kvart_resume_per_dev(existing, dict(existing))[2] == {"A"}, "same valid aggregate can resume")
    fresh = {**existing, "report_date": "02.01.2026"}
    _require(_kvart_resume_per_dev(existing, fresh)[2] == set(), "changed aggregate must invalidate cached payloads")
    fresh = {**existing, "developers": [{"наименование": "A", "квартиры_тыс_шт": "0.02"}]}
    _require(_kvart_resume_per_dev(existing, fresh)[2] == set(), "changed quantities must invalidate cached payloads")


def main() -> int:
    test_load_kvart_resume()
    test_force_ignores_kvart_resume()
    test_upsert_region_snapshot()
    test_resume_per_dev()
    _require(_kvart_count_matches(50, "< 1"), "small developer fits an upper bound")
    _require(_kvart_count_matches(3, "< 1") and _kvart_count_matches(4, "< 1"), "observed small Moscow developers fit the source interval")
    _require(_kvart_count_matches(999, "< 1"), "exclusive bound allows 999")
    _require(not _kvart_count_matches(1000, "< 1"), "exclusive bound rejects 1000")
    _require(not _kvart_count_matches(100000, "< 1"), "huge stale aggregate is rejected")
    _require(_kvart_count_matches(1050, "1,1"), "rounded thousands honor their displayed precision")
    _require(not _kvart_count_matches(990, "1,1"), "outside rounding interval is rejected")
    print("kvartirografia resume checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
