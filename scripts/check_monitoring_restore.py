"""Bounded recovery checks using isolated workbooks and production-like roots."""
from __future__ import annotations

import json
import contextlib
import io
import os
from pathlib import Path
import socket
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "scripts"))

import pandas as pd
import restore_monitoring_processed as recovery


class RecoveryChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.base = Path(temporary.name)
        self.root, self.run = self.base / "production", self.base / "evidence"
        raw = self.root / "data/raw/realty/nashdom"
        raw.mkdir(parents=True)
        (self.root / "data/processed").mkdir()
        (self.root / "data/marts/realty").mkdir(parents=True)
        (self.root / "state").mkdir()
        (self.root / "state/runtime_status.json").write_text('{"status":"unchanged"}', encoding="utf-8")
        rows = pd.DataFrame({"УИН": ["repeated", "repeated", None, None],
                             "Группа компаний": ["Developer"] * 4,
                             "Год ввода по Мосстату": [2024] * 4,
                             "Общая площадь": [10.0, 20.0, 0.0, 0.0],
                             "Жилая площадь": [1.0, 2.0, 0.0, 0.0],
                             "Отрасли": ["Жилые объекты"] * 4,
                             "Группировка": ["Жилье"] * 4,
                             "Назначение": ["Жилье"] * 4})
        self.sources = []
        for day in (1, 2):
            path = raw / f"monitoring_2_0_2026070{day}.xlsx"
            with pd.ExcelWriter(path, engine="openpyxl") as book:
                rows.to_excel(book, sheet_name="Реестр РВ", index=False)
                rows.to_excel(book, sheet_name="Реестр ОКС", index=False)
                book.book.properties.title = f"Independent snapshot {day}"
            os.utime(path, ns=(1_780_000_000_000_000_000 + day * 1_000_000_000, 1_780_000_000_000_000_000 + day * 1_000_000_000))
            self.sources.append(path)
        legacy = recovery.parser.parse(self.sources[0]).drop(columns=recovery.PROVENANCE)
        legacy = legacy.drop_duplicates([c for c in legacy if c not in {"loaded_at", "value"}], keep="last")
        pd.to_pickle(legacy, self.root / recovery.TARGET)
        access = recovery.DataAccess(recovery.DataContext(self.root, use_marts=False))
        pd.to_pickle(access.load_monitoring_2_0(source_path=self.sources[-1]), self.root / recovery.MART)
        self.before = recovery.protected_inventory(self.root)

    def prepared(self):
        with recovery.offline(), contextlib.redirect_stdout(io.StringIO()):
            return recovery.prepare(self.root, self.run, expected_latest_rows=16)

    def test_preparation_is_isolated_and_reconciles_equal_null_uin_zero_rows(self):
        report = self.prepared()
        self.assertEqual(recovery.protected_inventory(self.root), self.before)
        self.assertEqual(report["candidate_rows"], 32)
        self.assertEqual(report["legacy_rows"], 8)
        self.assertEqual(report["restored_rows_in_existing_snapshots"], 8)
        self.assertEqual(report["added_rows_from_previously_missing_snapshots"], 16)
        self.assertTrue(report["mart"]["exact_payload_equality"])
        frame = pd.read_pickle(self.run / "candidate.pkl")
        self.assertEqual(int(frame["uin"].isna().sum()), 16)
        self.assertEqual(int(frame["value"].eq(0).sum()), 16)
        self.assertEqual(sum(s["exact_cell_matches"] for s in report["sources"]), 32)

    def test_publication_changes_only_target_and_verified_backup_rolls_back(self):
        self.prepared()
        journal = recovery.publish(self.root, self.run)
        self.assertEqual(journal["changed_production_files"], [recovery.TARGET])
        self.assertEqual(recovery.fingerprint(self.run / "backup.pkl"), self.before[recovery.TARGET])
        self.assertEqual(len(pd.read_pickle(self.root / recovery.TARGET)), 32)
        recovery.rollback(self.root, self.run)
        self.assertEqual(recovery.protected_inventory(self.root), self.before)
        self.assertFalse((self.root / "state/.etl.lock").exists())

    def test_failure_after_replace_restores_original_bytes_and_mtime(self):
        self.prepared()
        def fail():
            raise OSError("injected post-replace failure")
        with self.assertRaisesRegex(OSError, "injected"):
            recovery.publish(self.root, self.run, after_replace=fail)
        self.assertEqual(recovery.protected_inventory(self.root), self.before)
        journal = json.loads((self.run / "publication.json").read_text(encoding="utf-8"))
        self.assertEqual(journal["phase"], "failed_rolled_back")

    def test_concurrent_production_change_refuses_publication(self):
        self.prepared()
        target = self.root / recovery.TARGET
        pd.to_pickle(pd.DataFrame({"newer": [1]}), target)
        concurrent_hash = recovery.file_sha256(target)
        with self.assertRaisesRegex(RuntimeError, "Production changed"):
            recovery.publish(self.root, self.run)
        self.assertEqual(recovery.file_sha256(target), concurrent_hash)
        self.assertFalse((self.run / "backup.pkl").exists())

    def test_source_and_candidate_tampering_are_rejected(self):
        self.prepared()
        original = self.sources[0].read_bytes()
        self.sources[0].write_bytes(original + b"source changed")
        with self.assertRaisesRegex(RuntimeError, "Production changed"):
            recovery.publish(self.root, self.run)
        self.assertEqual(recovery.fingerprint(self.root / recovery.TARGET), self.before[recovery.TARGET])
        (self.run / "candidate.pkl").write_bytes(b"bad candidate")
        with self.assertRaisesRegex(RuntimeError, "Candidate changed"):
            recovery.publish(self.root, self.run)

    def test_existing_etl_lock_is_preserved(self):
        self.prepared()
        lock = self.root / "state/.etl.lock"
        lock.write_text("belongs to another process", encoding="utf-8")
        with self.assertRaises(FileExistsError):
            recovery.publish(self.root, self.run)
        self.assertEqual(lock.read_text(encoding="utf-8"), "belongs to another process")
        self.assertEqual(recovery.fingerprint(self.root / recovery.TARGET), self.before[recovery.TARGET])

    def test_rollback_preserves_newer_version_and_rejects_corrupt_backup(self):
        self.prepared()
        recovery.publish(self.root, self.run)
        target = self.root / recovery.TARGET
        pd.to_pickle(pd.DataFrame({"newer": [2]}), target)
        concurrent_hash = recovery.file_sha256(target)
        with self.assertRaisesRegex(RuntimeError, "not this recovery version"):
            recovery.rollback(self.root, self.run)
        self.assertEqual(recovery.file_sha256(target), concurrent_hash)
        (self.run / "backup.pkl").write_bytes(b"corrupt")
        with self.assertRaisesRegex(RuntimeError, "Backup changed"):
            recovery.rollback(self.root, self.run)

    def test_unexpected_independent_cell_value_stops_preparation(self):
        parsed = recovery.parser.parse(self.sources[0])
        parsed.loc[0, "value"] += 1
        with self.assertRaisesRegex(RuntimeError, "source-cell reconciliation failed"):
            recovery.reconcile_cells(parsed, self.sources[0])

    def test_offline_guard_denies_network_and_restores_environment(self):
        previous = os.environ.get("TDM_DISABLED")
        with recovery.offline():
            self.assertEqual(os.environ["TDM_DISABLED"], "1")
            with self.assertRaisesRegex(RuntimeError, "Network is forbidden"):
                socket.create_connection(("127.0.0.1", 80))
        self.assertEqual(os.environ.get("TDM_DISABLED"), previous)


if __name__ == "__main__":
    unittest.main(verbosity=2)
