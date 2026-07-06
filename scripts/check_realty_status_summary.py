"""Self-check dashboard summary logic for realty_update_status.json."""
from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from app import audit  # noqa: E402
from app.audit import REALTY_RUNNING_STALE_MIN, realty_marts_status, realty_update_status_summary  # noqa: E402


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

    legacy_no_heartbeat = realty_update_status_summary({
        "failures": [],
        "marts_ok": True,
    })
    _assert_equal(legacy_no_heartbeat["status"], "unknown", "legacy without heartbeat status")
    _assert_equal(legacy_no_heartbeat["label"], "нет статуса", "legacy without heartbeat label")
    _assert_equal(legacy_no_heartbeat["warnings"], ["legacy", "no heartbeat"], "legacy without heartbeat warnings")

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

    original_manifest = audit.load_realty_marts_manifest
    original_current_sources = audit._current_realty_sources
    try:
        audit.load_realty_marts_manifest = lambda: {
            "built_at": "2026-07-03T12:00:00",
            "marts": {
                "sample": {
                    "built_at": "2026-07-03T12:00:00",
                    "summary": {"type": "dataframe", "rows": 10, "cols": 2},
                    "sources": [
                        {"path": "data/raw/realty/source.xlsx", "mtime": "2026-07-03T10:00:00"},
                    ],
                },
            },
        }

        def fail_if_scanned(_mart: str):
            raise AssertionError("default realty_marts_status should use manifest sources")

        audit._current_realty_sources = fail_if_scanned
        marts = realty_marts_status()
        _assert_equal(marts.loc[0, "status"], "ok", "manifest-only mart status")

        class NewerSource:
            def stat(self):
                class Stat:
                    st_mtime = datetime(2026, 7, 4, 10, 0, 0).timestamp()
                return Stat()

        audit._current_realty_sources = lambda _mart: [NewerSource()]
        live = realty_marts_status(live_check=True)
        _assert_equal(live.loc[0, "status"], "stale", "live mart status")
    finally:
        audit.load_realty_marts_manifest = original_manifest
        audit._current_realty_sources = original_current_sources

    print("realty status summary checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
