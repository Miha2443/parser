"""Permanent-history contracts; all files and collector responses are isolated."""
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor
from contextlib import ExitStack, redirect_stdout
from contextlib import contextmanager
from io import StringIO
import json
import multiprocessing
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))
from pipeline import source_history as sh
from pipeline.download_provenance import record_download, receipt_path
from pipeline import deduplicate as dd
import update_realty as ur


def archive_worker(args):
    root, name = args
    return sh.archive_file(Path(root) / name, root=root, indicator_key="parallel",
                           reason="test", move=True)


class SourceHistoryTests(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.downloads = self.root / "downloads"
        self.downloads.mkdir()
        self.history = sh.SourceHistory(self.root)
        self.stack.enter_context(redirect_stdout(StringIO()))

    def workbook(self, name, value=1):
        from openpyxl import Workbook
        path = self.downloads / name
        book = Workbook()
        book.active.append(["value", value])
        book.save(path)
        return path

    def records(self, archive=None):
        return list(sh._records(archive or self.history.archive))

    def test_same_name_replacement_preserves_bytes_and_original_receipt(self):
        old = self.workbook("current.xlsx", 1)
        record_download(old, source_id="31074_часть1", remote_date="12.10.2026")
        receipt = json.loads(receipt_path(old).read_text())
        original = old.read_bytes()
        candidate = self.workbook("staged.xlsx", 2)
        self.history.install(candidate, old, indicator_key="fedstat:31074_часть1", remote_date="13.10.2026")
        record = self.records()[0]
        self.assertEqual((self.root / record["archive_path"]).read_bytes(), original)
        self.assertEqual(record["download_receipt"], receipt)
        self.assertEqual(json.loads(receipt_path(old).read_text())["remote_date"], "13.10.2026")

    def test_invalid_or_unarchivable_candidate_never_overwrites(self):
        old = self.workbook("current.xlsx")
        original = old.read_bytes()
        candidate = self.downloads / "staged.xlsx"
        candidate.write_bytes(b"<html>unavailable</html>")
        with self.assertRaises(ValueError):
            self.history.install(candidate, old, indicator_key="rosstat:residential")
        self.assertEqual(old.read_bytes(), original)
        candidate = self.workbook("staged.xlsx", 2)
        with patch.object(sh.shutil, "copy2", side_effect=OSError("archive disk denied")):
            with self.assertRaises(OSError):
                self.history.install(candidate, old, indicator_key="rosstat:residential")
        self.assertEqual(old.read_bytes(), original)
        self.assertTrue(candidate.exists())

    def test_receipt_write_then_raise_restores_workbook_and_receipt(self):
        old = self.workbook("current.xlsx")
        record_download(old, source_id="43246", remote_date="12.10.2026")
        original, receipt = old.read_bytes(), receipt_path(old).read_bytes()
        candidate = self.workbook("staged.xlsx", 3)
        def broken(path, **kwargs):
            record_download(path, **kwargs)
            raise OSError("after atomic receipt replacement")
        with patch.object(sh, "record_download", side_effect=broken):
            with self.assertRaises(OSError):
                self.history.install(candidate, old, indicator_key="fedstat:43246", remote_date="13.10.2026")
        self.assertEqual(old.read_bytes(), original)
        self.assertEqual(json.loads(receipt_path(old).read_bytes()), json.loads(receipt))

    def test_legacy_retirement_is_exact_and_parts_independent(self):
        for key in ("31074_часть1", "31074_часть2", "34118_часть1", "34118_часть2", "43246", "57824"):
            with self.subTest(key=key):
                suffix = key + ".xlsx"
                current = self.workbook("20261013_" + suffix)
                old = self.workbook("20261012_" + suffix)
                neighbor = self.workbook("20261011_" + suffix + ".manual.xlsx")
                combined = self.workbook("20261011_34118_история.xlsx")
                records = self.history.retire("fedstat:" + key, current, remote_date="13.10.2026", migrate_legacy=True)
                self.assertEqual(len(records), 1)
                self.assertFalse(old.exists())
                self.assertTrue(current.exists() and neighbor.exists() and combined.exists())

    def test_receipts_veto_legacy_inference_and_future_files(self):
        current = self.workbook("20261013_salary.xlsx")
        old = self.workbook("20261012_salary.xlsx")
        record_download(current, source_id="wrong", remote_date="13.10.2026")
        self.assertFalse(self.history.retire("fedstat:43246", current, remote_date="13.10.2026", migrate_legacy=True))
        record_download(current, source_id="43246", remote_date="13.10.2026")
        record_download(old, source_id="57824", remote_date="12.10.2026")
        self.assertFalse(self.history.retire("fedstat:43246", current, remote_date="13.10.2026", migrate_legacy=True))
        receipt_path(old).write_text("broken json")
        self.assertFalse(self.history.retire("fedstat:43246", current, remote_date="13.10.2026", migrate_legacy=True))
        self.assertTrue(old.exists())

    def test_verified_retirement_keeps_receipt_metadata(self):
        old = self.workbook("old.xlsx")
        record_download(old, source_id="34118_часть1", remote_date="12.10.2026")
        current = self.workbook("current.xlsx", 2)
        record_download(current, source_id="34118_часть1", remote_date="13.10.2026")
        other = self.workbook("part2.xlsx")
        record_download(other, source_id="34118_часть2", remote_date="12.10.2026")
        records = self.history.retire("fedstat:34118_часть1", current, remote_date="13.10.2026")
        self.assertEqual(len(records), 1)
        self.assertEqual(records[0]["download_receipt"]["remote_date"], "12.10.2026")
        self.assertFalse(old.exists() or receipt_path(old).exists())
        self.assertTrue(other.exists())

    def test_legacy_current_changed_while_waiting_for_lock_never_retires(self):
        current = self.workbook("20261013_salary.xlsx")
        old = self.workbook("20261012_salary.xlsx")
        original_lock = sh._lock
        @contextmanager
        def replace_before_lock(directory):
            self.workbook(current.name, 42)
            with original_lock(directory):
                yield
        with patch.object(sh, "_lock", replace_before_lock):
            self.assertEqual(self.history.retire("fedstat:43246", current,
                remote_date="13.10.2026", migrate_legacy=True), [])
        self.assertTrue(old.exists())

    def test_source_and_archive_cannot_escape_declared_root(self):
        with tempfile.TemporaryDirectory() as other:
            outside = Path(other) / "manual.bin"
            outside.write_bytes(b"manual")
            source = self.root / "source.bin"
            source.write_bytes(b"source")
            with self.assertRaises(ValueError):
                sh.archive_file(outside, root=self.root, indicator_key="test", reason="test", move=True)
            with self.assertRaises(ValueError):
                sh.archive_file(source, root=self.root, archive_root=Path(other), indicator_key="test", reason="test", move=True)
            self.assertTrue(source.exists() and outside.exists())

    def test_offline_migration_uses_known_saved_current_only(self):
        import fedstat_checker as fs
        key = "43246"
        name = fs.current_filename(key, "13.10.2026")
        self.assertTrue(name)
        current = self.workbook(name)
        old = self.workbook("20261012_" + name[9:])
        unknown = self.workbook("20261011_manual_salary.xlsx")
        state = self.root / "state/fedstat_state.json"
        state.parent.mkdir()
        state.write_text(json.dumps({key: "13.10.2026", "unknown": "13.10.2026"}))
        self.assertEqual(len(sh.migrate_downloads(self.root)), 1)
        self.assertTrue(current.exists() and unknown.exists())
        self.assertFalse(old.exists())
        self.assertEqual(sh.migrate_downloads(self.root), [])

    def test_copy_then_move_deduplicates_and_durable_sidecar(self):
        source = self.root / "raw.bin"
        source.write_bytes(b"same day")
        kwargs = dict(root=self.root, indicator_key="realty:kvart:aggregate", deduplicate=True)
        first = sh.archive_file(source, reason="before_source_run", **kwargs)
        second = sh.archive_file(source, reason="older_active_version", move=True, **kwargs)
        self.assertEqual(first["archive_path"], second["archive_path"])
        self.assertEqual(len(self.records()), 1)
        self.assertFalse(source.exists())
        self.assertEqual(json.loads((self.root / first["archive_path"]).parent.joinpath("archive-record.json").read_text()), first)

    def test_index_failure_keeps_active_and_truncated_tail_does_not_swallow_record(self):
        source = self.root / "raw.bin"
        source.write_bytes(b"source")
        self.history.archive.mkdir(parents=True)
        index = self.history.archive / "index.jsonl"
        index.mkdir()  # Simulate an append failure after the sidecar was saved.
        with self.assertRaises(OSError):
            sh.archive_file(source, root=self.root, indicator_key="test", reason="test", move=True)
        self.assertTrue(source.exists())
        self.assertTrue(list(self.history.archive.rglob("archive-record.json")))
        index.rmdir()
        index.write_bytes(b'{"truncated":')
        record = sh.archive_file(source, root=self.root, indicator_key="test", reason="test", move=True)
        self.assertEqual(self.records(), [record])

    def test_thread_and_process_archives_are_collision_free(self):
        for batch, executor in (("threads", ThreadPoolExecutor(max_workers=4)),
                                ("processes", ProcessPoolExecutor(max_workers=2, mp_context=multiprocessing.get_context("spawn")))):
            with executor:
                names = [f"{batch}_{i}.bin" for i in range(4)]
                for name in names:
                    (self.root / name).write_bytes(name.encode())
                records = list(executor.map(archive_worker, [(str(self.root), name) for name in names]))
            self.assertEqual(len({r["archive_path"] for r in records}), 4)
            for record in records:
                self.assertTrue((self.root / record["archive_path"]).exists())
        self.assertEqual(len(self.records()), 8)

    def test_collector_partial_and_invalid_download_keep_previous(self):
        import fedstat_checker as fs
        import rosstat_checker as rs
        old = self.workbook("current.xlsx")
        original = old.read_bytes()
        with patch.object(fs, "_download_excel_staged", return_value=None):
            self.assertIsNone(fs.download_excel("43246", self.downloads, remote_date="13.10.2026", save_path_override=old))
        response = Mock()
        response.iter_content.return_value = [b"<html>server error</html>"]
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        session = Mock()
        session.get.return_value = response
        self.assertFalse(rs.download_file(session, "https://example.test/file.xlsx", "https://example.test", old, source_key="housing", remote_date="13.10.2026"))
        self.assertEqual(old.read_bytes(), original)

    def test_raw_backup_blocks_launch_on_failure_and_preserves_before_failed_run(self):
        raw = self.root / "data/raw/realty/monitoring/sample_20261013.json"
        raw.parent.mkdir(parents=True)
        raw.write_bytes(b"previous")
        with patch.object(ur, "ROOT", self.root), patch.object(ur, "REALTY_ROOT", self.root / "data/raw/realty"), \
                patch.object(ur, "snapshot_scope_for_source", return_value=([], [])), \
                patch.object(ur, "snapshot_files", return_value={"realty:monitoring/sample_20261013.json": (1, 1)}), \
                patch.object(ur.subprocess, "Popen", side_effect=OSError("failed to launch")) as child:
            ur._RUN_CANCELLED.clear()
            with patch.object(ur, "archive_file", side_effect=OSError("disk denied")):
                self.assertFalse(ur.run_source("monitoring", {}))
            child.assert_not_called()
            self.assertFalse(ur.run_source("monitoring", {}))
            child.assert_called_once()
        record = self.records(raw.parents[1] / "_archive")[0]
        self.assertEqual((self.root / record["archive_path"]).read_bytes(), b"previous")
        self.assertEqual(raw.read_bytes(), b"previous")

    def test_rotation_archives_our_logs_and_runs_without_touching_unrelated(self):
        logs = self.root / "logs"
        logs.mkdir()
        oldlogs = []
        for i in range(22):
            p = logs / f"update_20261012_0000{i:02d}.log"
            p.write_text(f"log {i}")
            oldlogs.append(p)
        unrelated = logs / "update_manual.log"
        unrelated.write_text("manual")
        status = self.root / "data/processed/realty_update_status.json"
        runs = status.parent / "realty_update_runs"
        runs.mkdir(parents=True)
        for i in range(3):
            (runs / f"run_20261012_000000_{i:06d}_00000000.json").write_text(json.dumps({"run": i}))
        (runs / "run_manual.json").write_text("manual")
        with patch.object(ur, "ROOT", self.root), patch.object(ur, "LOG_DIR", logs), \
                patch.object(ur, "REALTY_STATUS_FILE", status), patch.object(ur, "RUN_HISTORY_LIMIT", 2):
            ur._setup_logging()
            ur._close_logging()
            ur._STATUS_WRITE_ERRORS.clear()
            self.assertTrue(ur.write_realty_status({"run_id": "20261013_000000_000000_00000000", "status": "success"}))
            self.assertFalse(ur._STATUS_WRITE_ERRORS)
        self.assertEqual(len(self.records(logs / "_archive")), 3)
        self.assertEqual(len(self.records(status.parent / "_archive/realty_update_runs")), 2)
        self.assertTrue(unrelated.exists() and (runs / "run_manual.json").exists())
        self.assertEqual((self.root / self.records(logs / "_archive")[0]["archive_path"]).read_text(), "log 0")

    def test_rotation_failure_preserves_old_file_and_success_status(self):
        status = self.root / "data/processed/realty_update_status.json"
        runs = status.parent / "realty_update_runs"
        runs.mkdir(parents=True)
        old = runs / "run_20261012_000000_000000_00000000.json"
        old.write_text("old status")
        with patch.object(ur, "ROOT", self.root), patch.object(ur, "REALTY_STATUS_FILE", status), \
                patch.object(ur, "RUN_HISTORY_LIMIT", 1), patch.object(ur, "archive_file", side_effect=OSError("disk denied")):
            ur._STATUS_WRITE_ERRORS.clear()
            self.assertTrue(ur.write_realty_status({"run_id": "20261013_000000_000000_00000000", "status": "success"}))
            self.assertFalse(ur._STATUS_WRITE_ERRORS)
        self.assertEqual(old.read_text(), "old status")

    def test_destructive_dedup_uses_only_latest_active_version(self):
        active = self.root / "data/raw/realty/source"
        active.mkdir(parents=True)
        archive = self.root / "data/raw/realty/_archive/old"
        archive.mkdir(parents=True)
        (archive / "value_20261010.xlsx").write_bytes(b"A")
        new = active / "value_20261013.xlsx"
        new.write_bytes(b"A")
        with patch.object(dd, "REALTY_ROOT", active.parent), patch.object(dd, "ARCHIVE_ROOT", archive.parent):
            self.assertEqual(dd.deduplicate(new), (new, True))  # Archive only.
            older = active / "value_20261011.xlsx"
            older.write_bytes(b"A")
            latest = active / "value_20261012.xlsx"
            latest.write_bytes(b"B")
            os.utime(older, ns=(100, 100))
            os.utime(latest, ns=(200, 200))
            self.assertEqual(dd.deduplicate(new), (new, True))  # A -> B -> A.
            new.write_bytes(b"B")
            self.assertEqual(dd.deduplicate(new), (latest, False))
            self.assertFalse(new.exists())


if __name__ == "__main__":
    unittest.main()
