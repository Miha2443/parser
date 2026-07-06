"""Fast self-checks for scripts/update_realty.py planning logic.

No network, Selenium, or Excel parsing is used here. The goal is to catch
regressions in source group expansion and source -> mart selection.
"""
from __future__ import annotations

import time
from pathlib import Path

import update_realty as ur


def _assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def main() -> int:
    sources, unknown = ur.expand_requested_sources(["nashdom"])
    _assert_equal(sources, ["monitoring", "rasprod", "kvart"], "nashdom expansion")
    _assert_equal(unknown, [], "nashdom unknown")

    sources, unknown = ur.expand_requested_sources(["erzrf", "monitoring", "bad"])
    _assert_equal(
        sources,
        ["erz-top", "erz-cards", "monitoring"],
        "mixed group expansion",
    )
    _assert_equal(unknown, ["bad"], "unknown collection")

    sources, unknown = ur.expand_requested_sources(["all"])
    _assert_equal(unknown, [], "all unknown")
    _assert_equal(
        sources,
        ["monitoring", "rasprod", "kvart", "erz-top", "erz-cards", "fedstat", "rosstat"],
        "all expansion",
    )

    original_manifest = ur.REALTY_MARTS_MANIFEST
    try:
        ur.REALTY_MARTS_MANIFEST = Path("__definitely_missing_manifest__.json")
        _assert_equal(
            ur.select_realty_marts_for_sources(["monitoring"]),
            None,
            "missing manifest forces full build",
        )
    finally:
        ur.REALTY_MARTS_MANIFEST = original_manifest

    selected = ur.select_realty_marts_for_sources(["rasprod", "erz-top"])
    if selected is not None:
        _assert_equal(
            sorted(selected),
            ["erzrf_top", "escrow_manual", "rasprodannost"],
            "affected marts for rasprod+erz-top",
        )

    selected = ur.select_realty_marts_for_sources(["fedstat"])
    if selected is not None:
        _assert_equal(selected, set(), "fedstat does not touch realty marts")

    selected = ur.select_realty_marts_for_changes([
        "realty:nashdom/rasprodannost_20260702.xlsx",
        "realty:erzrf/top_developers_rf_20260702.json",
    ])
    if selected is not None:
        _assert_equal(
            sorted(selected),
            ["erzrf_top", "escrow_manual", "rasprodannost"],
            "affected marts from changed paths",
        )

    selected = ur.select_realty_marts_for_changes([])
    if selected is not None:
        _assert_equal(selected, set(), "no changed paths skips marts")

    original_repair = ur.select_repair_realty_marts
    try:
        ur.select_repair_realty_marts = lambda: {"escrow_manual"}
        selected = ur.select_realty_marts_for_changes([])
        if selected is not None:
            _assert_equal(selected, {"escrow_manual"}, "repair marts are selected")
    finally:
        ur.select_repair_realty_marts = original_repair

    captured = {}
    original_write_status = ur.write_realty_status
    try:
        ur.write_realty_status = lambda payload: captured.update(payload)
        started = time.time()
        ur.write_realty_run_status(
            "running",
            started=started,
            sources=["monitoring"],
            log_path=None,
        )
        _assert_equal(captured["status"], "running", "running status is written")
        _assert_equal(captured["finished_at"], None, "running status has no finish time")
        _assert_equal(captured["sources_requested"], ["monitoring"], "status sources")

        ur.write_realty_run_status(
            "failed",
            started=started,
            sources=["monitoring"],
            log_path=None,
            failures=["monitoring"],
        )
        _assert_equal(captured["status"], "failed", "failed status is written")
        _assert_equal(captured["failures"], ["monitoring"], "status failures")

        ur.set_active_realty_run(
            started=started,
            sources=["monitoring"],
            log_path=None,
            successes=[],
            failures=[],
            archive=True,
            keep=1,
            force=False,
            full_rasprod_history=False,
            selenium_sleep_scale="0.8",
        )
        ur.mark_active_realty_run_failed(RuntimeError("synthetic"))
        _assert_equal(captured["status"], "failed", "active failed status is written")
        _assert_equal(captured["error"], "RuntimeError: synthetic", "active failed error")
        ur.clear_active_realty_run()

        successes = ["monitoring"]
        failures = []
        ur.set_active_realty_run(
            started=started,
            sources=["monitoring", "rasprod"],
            log_path=None,
            successes=successes,
            failures=failures,
            archive=True,
            keep=1,
            force=False,
            full_rasprod_history=False,
            selenium_sleep_scale="0.8",
        )
        ur.write_active_realty_run_progress(
            current_stage="wave 1",
            last_completed_source="monitoring",
            last_completed_ok=True,
        )
        _assert_equal(captured["status"], "running", "progress status is running")
        _assert_equal(captured["completed_sources"], ["monitoring"], "progress completed sources")
        _assert_equal(captured["pending_sources"], ["rasprod"], "progress pending sources")
        _assert_equal(captured["last_completed_source"], "monitoring", "progress last source")
        ur.clear_active_realty_run()
    finally:
        ur.clear_active_realty_run()
        ur.write_realty_status = original_write_status

    _assert_equal(
        ur.source_alias_for_changed_path("downloads:Введено в действие общей площади жилых домов.xlsx"),
        "rosstat",
        "downloads rosstat mapping",
    )

    print("update_realty plan checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
