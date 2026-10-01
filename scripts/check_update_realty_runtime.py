"""Isolated lifecycle regressions: never download or write production status/data."""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import Mock, patch

import update_realty as ur


class RuntimeChecks(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.status = self.root / "data/processed/realty_update_status.json"
        for name, value in {
            "ROOT": self.root, "REALTY_ROOT": self.root / "data/raw/realty",
            "LOG_DIR": self.root / "logs", "PROCESSED_DIR": self.status.parent,
            "REALTY_STATUS_FILE": self.status,
            "REALTY_MARTS_MANIFEST": self.root / "data/marts/realty/manifest.json",
            "REALTY_UPDATE_LOCK": self.root / "state/.realty_update.lock",
            "SNAPSHOT_DIRS": [(self.root / "data/raw/realty", "realty", "")],
            "HEARTBEAT_INTERVAL_SEC": 0.02,
        }.items():
            self.stack.enter_context(patch.object(ur, name, value))
        self.stack.enter_context(patch.dict(os.environ, {"TDM_DISABLED": "1"}))
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.source = self.stack.enter_context(patch.object(ur, "run_source", return_value=True))
        self.processed = self.stack.enter_context(patch.object(ur, "build_processed_pickles", return_value=True))
        self.marts = self.stack.enter_context(patch.object(ur, "build_realty_marts", return_value=True))
        self.stack.enter_context(patch.object(ur, "select_repair_realty_marts", return_value={"erzrf_top"}))
        self.stack.enter_context(patch.object(ur, "check_escrow"))
        self.stack.enter_context(patch.object(ur, "deduplicate_new_files", return_value=(0, 0)))
        self.stack.enter_context(patch.object(ur, "archive_old", return_value=True))
        self.stack.enter_context(patch.object(ur, "archive_old_for_source", return_value=True))
        # A missed mock must fail before launching any downloader/build command.
        self.stack.enter_context(patch.object(ur.subprocess, "Popen", side_effect=AssertionError("unexpected child")))
        self.stack.enter_context(patch.object(ur, "_kill_process_tree"))
        ur._RUN_CANCELLED.clear()
        ur._STATUS_WRITE_ERRORS.clear()
        ur.clear_active_realty_run()
        self.addCleanup(ur._STATUS_WRITE_ERRORS.clear)

    def run_update(self, *extra):
        with patch.object(sys, "argv", ["update_realty.py", "monitoring", "--no-notify",
                                        "--no-archive", "--retries", "0", *extra]):
            return ur.main()

    def payload(self):
        return json.loads(self.status.read_text(encoding="utf-8"))

    def test_success_scope_history_and_unique_logs(self):
        self.assertEqual(self.run_update(), 0)
        first = self.payload()
        self.processed.assert_called_once_with(only={"realty_monitoring_2_0"})
        self.marts.assert_called_once_with(
            only={"monitoring_2_0", "construction_operational"}
        )
        self.assertEqual(first["status"], "success")
        self.assertEqual(first["kvart_per_dev"], os.environ.get("KVART_PER_DEV", "1"))
        self.assertEqual(first["marts_repair_selected"], [])
        self.assertFalse(ur.REALTY_UPDATE_LOCK.exists())
        self.assertEqual(self.run_update(), 0)
        self.assertNotEqual(first["run_id"], self.payload()["run_id"])
        self.assertEqual(len(list((self.status.parent / "realty_update_runs").glob("*.json"))), 2)
        self.assertEqual(len(list(ur.LOG_DIR.glob("*.log"))), 2)

    def test_initial_status_failure_preserves_previous_and_stops_sources(self):
        self.status.parent.mkdir(parents=True)
        self.status.write_bytes(b'{"status":"old"}')
        before = self.status.read_bytes()
        with patch.object(Path, "replace", side_effect=OSError("disk denied")):
            self.assertEqual(self.run_update(), 2)
        self.source.assert_not_called()
        self.assertEqual(self.status.read_bytes(), before)
        self.assertFalse(list(self.status.parent.glob("*.tmp")))
        self.assertFalse(ur.REALTY_UPDATE_LOCK.exists())

    def test_terminal_status_failure_is_nonzero_and_preserves_previous(self):
        original = ur._write_json_atomic
        previous = []

        def fail_terminal(path, payload):
            if path == self.status and payload["status"] == "success":
                previous.append(path.read_bytes())
                raise OSError("terminal denied")
            return original(path, payload)

        with patch.object(ur, "_write_json_atomic", side_effect=fail_terminal):
            self.assertEqual(self.run_update(), 2)
        self.assertEqual(self.status.read_bytes(), previous[-1])
        history_file, = (self.status.parent / "realty_update_runs").glob("*.json")
        history = json.loads(history_file.read_text(encoding="utf-8"))
        self.assertEqual(history["status"], "failed")
        self.assertFalse(history["status_published"])
        self.assertIn("terminal denied", history["error"])
        self.assertTrue(history["status_write_errors"])

    def test_heartbeat_during_blocking_source_and_terminal_race(self):
        observed = []
        source_waiting = threading.Event()
        heartbeat_advanced = threading.Event()
        original = ur._write_json_atomic

        def observe_heartbeat(path, payload):
            original(path, payload)
            if (path == self.status and source_waiting.is_set()
                    and threading.current_thread().name == "realty-heartbeat"
                    and len(observed) < 2
                    and (not observed or payload["duration_sec"] > observed[-1]["duration_sec"])):
                observed.append(self.payload())
                if len(observed) == 2:
                    heartbeat_advanced.set()

        def slow_source(*args, **kwargs):
            source_waiting.set()
            self.assertTrue(heartbeat_advanced.wait(timeout=5),
                            "two advancing heartbeat writes were not observed within 5 seconds")
            return True

        self.source.side_effect = slow_source
        with patch.object(ur, "_write_json_atomic", side_effect=observe_heartbeat):
            self.assertEqual(self.run_update(), 0)
        self.assertIn("Волна", observed[0]["current_stage"])
        self.assertEqual(observed[0]["active_sources"], ["monitoring"])
        self.assertGreater(observed[1]["duration_sec"], observed[0]["duration_sec"])
        final_bytes = self.status.read_bytes()
        time.sleep(0.05)
        self.assertEqual(self.status.read_bytes(), final_bytes)
        self.assertIsNone(ur._HEARTBEAT_THREAD)

    def test_transient_heartbeat_write_failure_remains_failed(self):
        original = ur._write_json_atomic
        denied = False

        def deny_once(path, payload):
            nonlocal denied
            if path == self.status and payload.get("current_stage") == "snapshot after" and not denied:
                denied = True
                raise OSError("transient disk denied")
            return original(path, payload)

        with patch.object(ur, "_write_json_atomic", side_effect=deny_once):
            self.assertEqual(self.run_update(), 2)
        self.assertEqual(self.payload()["status"], "failed")
        self.assertTrue(self.payload()["status_write_errors"])

    def test_interrupt_during_processed_build_and_snapshot(self):
        self.processed.side_effect = KeyboardInterrupt
        self.assertEqual(self.run_update(), 130)
        self.assertEqual(self.payload()["status"], "interrupted")
        self.assertEqual(self.payload()["current_stage"], "processed build")
        self.assertFalse(ur.REALTY_UPDATE_LOCK.exists())
        with patch.object(ur, "snapshot_files", side_effect=KeyboardInterrupt):
            self.assertEqual(self.run_update(), 130)
        self.assertEqual(self.payload()["current_stage"], "snapshot before")

    def test_source_failure_and_build_crash(self):
        self.source.return_value = False
        self.assertEqual(self.run_update("--no-marts"), 2)
        self.assertEqual(self.payload()["failures"], ["monitoring"])
        self.source.return_value = True
        self.processed.side_effect = RuntimeError("synthetic build error")
        self.assertEqual(self.run_update(), 2)
        self.assertEqual(self.payload()["status"], "failed")
        self.assertIn("synthetic build error", self.payload()["error"])

    def test_history_rotation_does_not_touch_unrelated_file(self):
        with patch.object(ur, "RUN_HISTORY_LIMIT", 1):
            self.assertEqual(self.run_update(), 0)
            history = self.status.parent / "realty_update_runs"
            unrelated = history / "run_personal.json"
            unrelated.write_text("keep", encoding="utf-8")
            self.assertEqual(self.run_update(), 0)
            self.assertEqual(len(list(history.glob("*.json"))), 2)
            self.assertEqual(unrelated.read_text(), "keep")

    def test_archive_stdout_reaches_canonical_log(self):
        proc = Mock(pid=123, stdout=StringIO("archive detail\n"))
        proc.wait.return_value = 0
        with patch.object(ur.subprocess, "Popen", return_value=proc):
            path = ur._setup_logging()
            try:
                self.assertEqual(ur._run_logged_command(["synthetic"], "archive"), 0)
            finally:
                ur._close_logging()
        self.assertIn("[archive] archive detail", path.read_text(encoding="utf-8"))


def main():
    production = ur.REALTY_STATUS_FILE
    before = production.read_bytes() if production.exists() else None
    result = unittest.TextTestRunner(verbosity=2).run(unittest.defaultTestLoader.loadTestsFromTestCase(RuntimeChecks))
    after = production.read_bytes() if production.exists() else None
    if after != before:
        raise AssertionError("production status changed during isolated tests")
    print("Production status unchanged: " + (hashlib.sha256(before).hexdigest() if before is not None else "absent"))
    return 0 if result.wasSuccessful() else 1


if __name__ == "__main__":
    raise SystemExit(main())
