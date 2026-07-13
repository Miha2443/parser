"""Fast self-checks for scripts/update_realty.py planning logic.

No network, Selenium, or Excel parsing is used here. The goal is to catch
regressions in source group expansion and source -> mart selection.
"""
from __future__ import annotations

import json
import tempfile
import time
from contextlib import redirect_stdout
from io import StringIO
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

    original_realty_root = ur.REALTY_ROOT
    try:
        with tempfile.TemporaryDirectory() as tmp:
            realty_root = Path(tmp) / "realty"
            nashdom = realty_root / "nashdom"
            nashdom.mkdir(parents=True)
            (nashdom / "monitoring_2_0_20260702.xlsx").write_bytes(b"monitoring")
            (nashdom / "~$monitoring_2_0_20260702.xlsx").write_bytes(b"office lock")
            (nashdom / "monitoring_2_0_20260702.xlsx.tmp").write_bytes(b"partial")
            (nashdom / "rasprodannost_20260702.xlsx").write_bytes(b"rasprod")
            (nashdom / "rasprodannost_20260702.xlsx.crdownload").write_bytes(b"partial")
            (nashdom / "kvartirografia_20260702.json").write_text("{}", encoding="utf-8")

            ur.REALTY_ROOT = realty_root
            roots, prefixes = ur.snapshot_scope_for_source("monitoring")
            snapshot = ur.snapshot_files(roots=roots, file_prefixes=prefixes)
            _assert_equal(
                sorted(snapshot),
                ["realty:nashdom/monitoring_2_0_20260702.xlsx"],
                "monitoring scoped snapshot ignores temp files",
            )

            roots, prefixes = ur.snapshot_scope_for_source("rasprod")
            snapshot = ur.snapshot_files(roots=roots, file_prefixes=prefixes)
            _assert_equal(
                sorted(snapshot),
                ["realty:nashdom/rasprodannost_20260702.xlsx"],
                "rasprod scoped snapshot ignores temp files",
            )

            target = nashdom / "monitoring_2_0_20260702.xlsx"
            roots, prefixes = ur.snapshot_scope_for_source("monitoring")
            first = ur.snapshot_files(roots=roots, file_prefixes=prefixes)
            second = ur.snapshot_files(roots=roots, file_prefixes=prefixes)
            _assert_equal(second, first, "unchanged snapshot should be stable")
            time.sleep(0.01)
            target.write_bytes(b"MONITORING")
            changed = ur.snapshot_files(roots=roots, file_prefixes=prefixes)
            if changed == first:
                raise AssertionError("snapshot cache did not detect same-size content change")
            cache_key = str(target.resolve())
            if cache_key not in ur._SNAPSHOT_DIGEST_CACHE:
                raise AssertionError("snapshot cache missing live file")
            target.unlink()
            removed = ur.snapshot_files(roots=roots, file_prefixes=prefixes)
            _assert_equal(removed, {}, "removed file should disappear from snapshot")
            if cache_key in ur._SNAPSHOT_DIGEST_CACHE:
                raise AssertionError("snapshot cache retained deleted file")
    finally:
        ur.REALTY_ROOT = original_realty_root

    original_run = ur.subprocess.run
    try:
        calls = []

        class Result:
            def __init__(self, returncode: int):
                self.returncode = returncode

        def fake_run_ok(cmd, cwd, check):
            calls.append((cmd, cwd, check))
            return Result(0)

        ur.subprocess.run = fake_run_ok
        with redirect_stdout(StringIO()):
            _assert_equal(ur.archive_old_for_source("monitoring", keep=2), True, "source archive success")
            _assert_equal(calls[-1][0][-2:], ["--prefixes", "monitoring_2_0_"], "source archive prefixes")
            _assert_equal(ur.archive_old_for_source("fedstat", keep=1), True, "source without archive scope succeeds")
            _assert_equal(ur.archive_old(keep=3), True, "final archive success")

        def fake_run_fail(cmd, cwd, check):
            return Result(7)

        ur.subprocess.run = fake_run_fail
        with redirect_stdout(StringIO()):
            _assert_equal(ur.archive_old_for_source("monitoring", keep=1), False, "source archive failure")
            _assert_equal(ur.archive_old(keep=1), False, "final archive failure")
    finally:
        ur.subprocess.run = original_run

    original_manifest = ur.REALTY_MARTS_MANIFEST
    try:
        ur.REALTY_MARTS_MANIFEST = Path("__definitely_missing_manifest__.json")
        _assert_equal(
            ur.select_realty_marts_for_sources(["monitoring"]),
            None,
            "missing manifest forces full build",
        )
        with tempfile.TemporaryDirectory() as tmp:
            manifest = Path(tmp) / "manifest.json"
            ur.REALTY_MARTS_MANIFEST = manifest

            manifest.write_text("{bad-json", encoding="utf-8")
            _assert_equal(
                ur.select_realty_marts_for_sources(["monitoring"]),
                None,
                "bad manifest forces full build",
            )

            manifest.write_text(json.dumps({"marts": {}}), encoding="utf-8")
            _assert_equal(
                ur.select_realty_marts_for_changes(["realty:nashdom/monitoring_2_0.xlsx"]),
                None,
                "empty manifest forces full build",
            )

            manifest.write_text(json.dumps({"marts": {"monitoring_2_0": {"file": "x.pkl"}}}), encoding="utf-8")
            _assert_equal(
                ur.has_valid_realty_marts_manifest(),
                True,
                "valid manifest is accepted",
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

    selected = ur.select_realty_marts_for_changes([
        "realty:vvod/vvod.xlsx",
        "realty:vvod/emiss_34118_base.xls",
    ])
    if selected is not None:
        _assert_equal(
            sorted(selected),
            ["emiss_34118", "escrow_manual", "vvod_static"],
            "affected marts from vvod static paths",
        )

    selected = ur.select_realty_marts_for_changes([
        "realty:escrow_manual/escrow_20260702.xlsx",
    ])
    if selected is not None:
        _assert_equal(
            sorted(selected),
            ["escrow_manual"],
            "affected marts from escrow manual paths",
        )

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
        if not captured.get("updated_at"):
            raise AssertionError("running status has no updated_at")
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
        if not captured.get("updated_at"):
            raise AssertionError("failed status has no updated_at")
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

    _assert_equal(
        ur.source_alias_for_changed_path("realty:vvod/Stroi_111_2025.xls"),
        "rosstat",
        "vvod rosstat mapping",
    )
    _assert_equal(
        ur.source_alias_for_changed_path("realty:escrow_manual/escrow.xlsx"),
        "escrow-manual",
        "escrow manual mapping",
    )

    _assert_equal(
        ur.realty_update_exit_code(failures=[], marts_ok=True, processed_ok=True, final_archive_ok=True),
        0,
        "clean run exit code",
    )
    _assert_equal(
        ur.realty_update_exit_code(failures=["monitoring"], marts_ok=True, processed_ok=True, final_archive_ok=True),
        2,
        "source failure exit code",
    )
    _assert_equal(
        ur.realty_update_exit_code(failures=[], marts_ok=False, processed_ok=True, final_archive_ok=True),
        2,
        "mart failure exit code",
    )
    _assert_equal(
        ur.realty_update_exit_code(failures=[], marts_ok=True, processed_ok=True, final_archive_ok=False),
        0,
        "final archive warning exit code",
    )
    _assert_equal(
        ur.realty_update_exit_code(failures=[], marts_ok=True, processed_ok=False, final_archive_ok=True),
        2,
        "processed failure exit code",
    )
    _assert_equal(
        ur.realty_update_error_message(
            failures=["monitoring"],
            marts_ok=False,
            processed_ok=False,
            final_archive_ok=False,
        ),
        "source failures: monitoring; realty marts failed; processed dashboard build failed",
        "combined update error message",
    )
    report = ur.build_tdm_report(
        successes=["monitoring"],
        failures=[],
        diff={"added": [], "changed": []},
        total_min=1.2,
        marts_ok=False,
        processed_ok=False,
        final_archive_ok=False,
        archive_warnings=["final archive failed"],
    )
    if "Marts build failed" not in report:
        raise AssertionError("TDM report should mention mart failure")
    if "Processed dashboard build failed" not in report:
        raise AssertionError("TDM report should mention processed failure")
    if "Архивация" not in report:
        raise AssertionError("TDM report should mention final archive warning")

    print("update_realty plan checks: ok")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
