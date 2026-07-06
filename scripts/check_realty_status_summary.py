"""Self-check dashboard summary logic for realty_update_status.json."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app.audit import REALTY_RUNNING_STALE_MIN, realty_update_status_summary  # noqa: E402


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    now = datetime.now().isoformat(timespec="seconds")

    ok = realty_update_status_summary({
        "status": "success",
        "updated_at": now,
        "failures": [],
        "marts_ok": True,
    })
    _assert_equal(ok["status"], "success", "success status")
    _assert_equal(ok["label"], "успех", "success label")
    _assert_equal(ok["warnings"], [], "success warnings")
    _assert_equal(ok["stale_running"], False, "success stale")

    legacy = realty_update_status_summary({
        "updated_at": now,
        "failures": [],
        "marts_ok": True,
    })
    _assert_equal(legacy["status"], "success", "legacy inferred status")
    _assert_equal(legacy["warnings"], ["legacy"], "legacy warning")

    stale_time = (datetime.now() - timedelta(minutes=REALTY_RUNNING_STALE_MIN + 5)).isoformat(timespec="seconds")
    stale = realty_update_status_summary({
        "status": "running",
        "updated_at": stale_time,
        "failures": [],
    })
    _assert_equal(stale["status"], "running", "stale running status")
    _assert_equal(stale["stale_running"], True, "stale running flag")
    _assert_equal(stale["label"], "возможно завис", "stale running label")

    bad_log = realty_update_status_summary({
        "status": "failed",
        "updated_at": now,
        "failures": ["monitoring"],
        "log_file": 123,
        "error": "synthetic",
    })
    _assert_equal(bad_log["warnings"], ["bad log_file"], "bad log warning")
    _assert_equal(bad_log["error"], "synthetic", "error passthrough")

    print("realty status summary checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
