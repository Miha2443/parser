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
            {"site_name": "A", "apartments": {"all": {"count": "10"}}},
            {"site_name": "B", "apartments": {"all": {"count": "20"}}},
            {"site_name": "C", "apartments": {"all": {"count": "30"}}},
        ],
        "per_dev_attempts": [
            {"site_name": "A", "status": "ok"},
            {"site_name": "B", "status": "switch_fail"},
            {"site_name": "C", "status": "rejected"},
        ],
    }

    per_dev, attempts, done = _kvart_resume_per_dev(existing)
    _require(done == {"A", "C"}, "resume should skip only successful/rejected attempts")
    _require([row["site_name"] for row in per_dev] == ["A", "C"], "per-dev rows should match reusable attempts")
    _require([row["site_name"] for row in attempts] == ["A", "C"], "attempts should keep reusable statuses")


def main() -> int:
    test_load_kvart_resume()
    test_force_ignores_kvart_resume()
    test_upsert_region_snapshot()
    test_resume_per_dev()
    print("kvartirografia resume checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
