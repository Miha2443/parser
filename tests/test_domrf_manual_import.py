"""Manual imports must retain independent sources and verified history."""
import copy
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from docker import domrf_import as d

REPO = Path(__file__).resolve().parents[1]


class DomrfManualImportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.capture = d.load_capture(REPO)

    def test_checked_in_capture_checksums_and_literal_current_data(self):
        from pipeline.downloaders.nashdom_operational_contract import validate_current_snapshot
        from pipeline.downloaders.nashdom_mortgage_api import normalize_month
        current = self.capture["ready_to_import/construction_operational_20261125.json"]
        validate_current_snapshot(current)
        self.assertEqual(len(self.capture["rasprodannost_dynamics_2020-01_2026-10.json"]), 164)
        for row in self.capture["mortgage_current_2026-09.json"]:
            exact = normalize_month(row["region"], row["period"], row["rawMetrics"]["exchanges"])
            self.assertEqual(exact["metrics"], row["metrics"])

    def test_missing_rasprod_history_cannot_be_marked_complete(self):
        with self.assertRaisesRegex(ValueError, "period set differs"):
            d.merge_rasprod([], self.capture["rasprodannost_current_2026-10.json"],
                           self.capture["rasprodannost_dynamics_2020-01_2026-10.json"])

    def test_changed_old_kpi_rejects_reuse_instead_of_flattening_history(self):
        current = self.capture["rasprodannost_current_2026-10.json"]
        dynamics = [r for r in self.capture["rasprodannost_dynamics_2020-01_2026-10.json"] if r["period"] == "2026-10"]
        previous = copy.deepcopy(current)
        previous[0]["kpi"][0]["значение"] = "1"
        with self.assertRaisesRegex(ValueError, "differs from captured"):
            d.merge_rasprod(previous, [], dynamics)
        merged, expected = d.merge_rasprod(previous, current, dynamics)
        self.assertEqual(len(merged), 2)
        self.assertEqual(len(expected), 2)

    def test_newer_rasprod_period_is_retained(self):
        old = copy.deepcopy(self.capture["rasprodannost_current_2026-10.json"])
        old[0]["month_num"] = 11
        with self.assertRaisesRegex(ValueError, "newer"):
            d.merge_rasprod(old, self.capture["rasprodannost_current_2026-10.json"],
                           self.capture["rasprodannost_dynamics_2020-01_2026-10.json"])

    def test_construction_rejects_newer_server_data_without_writing(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / d.RAW
            raw.mkdir(parents=True)
            incoming = self.capture["ready_to_import/construction_operational_20261125.json"]
            old = copy.deepcopy(incoming)
            for row in old["construction"]:
                row["report_period"] = "Декабрь 2026"
            file = raw / "construction_operational_20261201.json"
            file.write_text(json.dumps(old), encoding="utf-8")
            before = file.read_bytes()
            with self.assertRaises(ValueError):
                d.import_construction(root, self.capture)
            self.assertEqual(file.read_bytes(), before)
            self.assertFalse((raw / "construction_operational_20261125.json").exists())

    def test_failed_family_rolls_back_journal_and_files_other_family_is_admitted(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / d.RAW
            raw.mkdir(parents=True)
            old = raw / "monitoring_2_0_20261101.xlsx"
            old.write_bytes(b"old verified monitoring")
            os.utime(old, ns=(1700000000123456700, 1700000000123456700))
            journal = root / "data/processed/monitoring_2_0_changes.jsonl"
            journal.parent.mkdir(parents=True)
            journal.write_text('old journal\n', encoding="utf-8")
            def files(root, alias):
                return list((root / d.RAW).glob("monitoring_*.xlsx" if alias == "monitoring" else "construction_*.json" if alias == "construction" else "rasprodannost_*.json")) if alias in {"monitoring", "construction", "rasprod"} else []
            def bad_monitoring(candidate, _):
                old.write_bytes(b"invalid replacement")
                (raw / "monitoring_2_0_20261125.xlsx").write_bytes(b"invalid new file")
                journal.write_text('incorrect journal\n', encoding="utf-8")
                return True
            def good_construction(candidate, _):
                (raw / "construction_operational_20261125.json").write_text('{}')
                return True
            def failed(*_):
                raise ValueError("unconfirmed source")
            def validate(_, alias):
                if alias == "monitoring":
                    raise ValueError("bad workbook")
            with patch.object(d, "load_capture", return_value={}), patch.object(d, "source_files", files), \
                 patch.object(d, "import_monitoring", bad_monitoring), patch.object(d, "import_construction", good_construction), \
                 patch.object(d, "import_rasprod", failed), patch.object(d, "import_mortgage", failed), \
                 patch("docker.coverage.validate_source_raw", side_effect=validate):
                result = d.apply(root, REPO)
            self.assertEqual(result["construction"]["state"], "fresh")
            self.assertEqual(result["monitoring"]["state"], "retained")
            self.assertEqual(result["kvart"]["state"], "retained")
            self.assertEqual(old.read_bytes(), b"old verified monitoring")
            self.assertEqual(old.stat().st_mtime_ns, 1700000000123456700)
            self.assertEqual(journal.read_text(), 'old journal\n')
            self.assertFalse((raw / "monitoring_2_0_20261125.xlsx").exists())
            self.assertTrue((raw / "construction_operational_20261125.json").exists())

    def test_manual_metadata_is_truthful_and_inventory_is_checked(self):
        from pipeline.source_availability import read_publication, verify_inventory
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / d.RAW
            raw.mkdir(parents=True)
            source = raw / "construction_operational_20261125.json"
            source.write_text('{}')
            result = {"construction": {"state": "fresh", "reason": "verified manual capture"}}
            metadata = d.publication_metadata(root, result)
            target = root / "data/processed/publication_status.json"
            target.parent.mkdir(parents=True)
            target.write_text(json.dumps(metadata))
            parsed = read_publication(root)
            self.assertEqual(parsed["mode"], "manual_import")
            self.assertEqual(parsed["sources"]["construction"]["state"], "fresh")
            self.assertEqual(parsed["sources"]["construction"]["outcome"]["phase"], "manual_import")
            self.assertEqual(parsed["sources"]["fedstat"]["state"], "unavailable")
            verify_inventory(root, parsed)
            source.write_text('{"tampered":true}')
            with self.assertRaises(RuntimeError):
                verify_inventory(root, parsed)

    def test_mortgage_import_preserves_old_months_and_repeat_is_unchanged(self):
        from pipeline.downloaders.nashdom_mortgage import year_bundle
        from pipeline.downloaders.nashdom_mortgage_api import normalize_month
        from pipeline.mortgage_history import save_checkpoint, publish_history, load_published
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / d.RAW
            for row in self.capture["mortgage_current_2026-09.json"]:
                exchanges = copy.deepcopy(row["rawMetrics"]["exchanges"])
                for exchange in exchanges:
                    exchange["request"]["reportingPeriod"] = "2026-08-01"
                normalized = normalize_month(row["region"], "2026-08", exchanges)
                bundle = year_bundle(row["region"], 2026, [(8, normalized)])
                bundle["collectedAt"] = "2026-10-22T04:00:00+00:00"
                save_checkpoint(raw, bundle)
            previous = publish_history(raw, {r: [2026] for r in ("rf", "msk")})
            self.assertTrue(d.import_mortgage(root, self.capture))
            current = load_published(raw)
            self.assertEqual(len(current["records"]), 12)
            self.assertTrue(all(row in current["records"] for row in previous["records"]))
            self.assertFalse(d.import_mortgage(root, self.capture))

    def test_rasprod_workbook_matches_canonical_consumer_and_checksums(self):
        from pipeline.data_access import DataAccess, DataContext
        from docker.coverage import validate_rasprod_sources
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / d.RAW
            raw.mkdir(parents=True)
            entries = self.capture["rasprodannost_current_2026-10.json"]
            stamp = self.capture["ready_to_import/construction_operational_20261125.json"]["scraped_at"]
            d.write_rasprod(raw, entries, {(r, 2026, 10) for r in ("rf", "msk")}, stamp)
            access = DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))
            validate_rasprod_sources(access, access.load_rasprodannost())

    def test_publication_imports_from_live_baseline_not_mutable_working_files(self):
        from docker import publication as p
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "working"
            runtime = Path(directory) / "runtime"
            previous = runtime / "releases/prior"
            for base, text in ((root, 'unpublished working data'), (previous, 'live verified baseline')):
                (base / "downloads").mkdir(parents=True)
                (base / "downloads/marker.txt").write_text(text)
                (base / "data/processed").mkdir(parents=True)
            observed = []
            def apply(candidate, source):
                self.assertEqual(source, root)
                self.assertEqual((candidate / "downloads/marker.txt").read_text(), 'live verified baseline')
                observed.append(candidate)
                return {"monitoring": {"state": "fresh", "reason": "manual"}}
            with patch.object(p, "live_target", return_value="releases/prior"), \
                 patch("pipeline.construction_tree_history.prepare"), patch("pipeline.construction_tree_history.stage"), \
                 patch.object(d, "apply", side_effect=apply), \
                 patch.object(d, "publication_metadata", return_value={"optional_sources": {}, "sources": {}}), \
                 patch.object(p, "build_candidate_processed") as processed, patch.object(p, "build_candidate"), \
                 patch.object(p, "validate"), patch.object(p, "swap") as swap, \
                 patch("docker.mortgage.select_source", return_value={"state": "unavailable"}), \
                 patch("docker.preferential.select_source", return_value={"state": "unavailable"}), \
                 patch("docker.preferential.verify_snapshot"):
                p.publish(root, runtime, domrf_import=True)
            self.assertEqual(len(observed), 1)
            self.assertEqual(processed.call_args.args[1], {"monitoring"})
            self.assertEqual(swap.call_count, 1)
            pending = json.loads((runtime / "pending.json").read_text())
            self.assertEqual(pending["previous"], "releases/prior")
            self.assertEqual((runtime / pending["next"] / "downloads/marker.txt").read_text(), 'live verified baseline')
            self.assertEqual((root / "downloads/marker.txt").read_text(), 'unpublished working data')

    def test_bad_import_never_switches_live_or_creates_pending_journal(self):
        from docker import publication as p
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / "working"
            runtime = Path(directory) / "runtime"
            (runtime / "releases/prior").mkdir(parents=True)
            with patch.object(p, "live_target", return_value="releases/prior"), \
                 patch("pipeline.construction_tree_history.prepare"), \
                 patch.object(d, "apply", side_effect=RuntimeError("bad capture")), patch.object(p, "swap") as swap:
                with self.assertRaisesRegex(RuntimeError, "bad capture"):
                    p.publish(root, runtime, domrf_import=True)
            swap.assert_not_called()
            self.assertFalse((runtime / "pending.json").exists())
            self.assertEqual(list((runtime / "releases").iterdir()), [runtime / "releases/prior"])


if __name__ == "__main__":
    unittest.main()
