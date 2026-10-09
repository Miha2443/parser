"""Publication contracts without Docker or network access."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from docker import publication as p

REPO = Path(__file__).resolve().parents[1]


class PublicationTests(unittest.TestCase):
    def test_snapshot_is_independent_and_preserves_manifest_timestamps(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "source", root / "snapshot"
            source.mkdir()
            original = source / "input.xlsx"
            original.write_bytes(b"old workbook")
            os.utime(original, ns=(1700000000123456700, 1700000000123456700))
            (source / "_archive").mkdir()
            (source / "_archive/old.xlsx").write_bytes(b"archive")
            (source / "partial.crdownload").write_bytes(b"partial")
            p.copy_tree(source, target)
            self.assertEqual(original.stat().st_mtime_ns, (target / original.name).stat().st_mtime_ns)
            original.write_bytes(b"new mutable workbook")
            self.assertEqual((target / original.name).read_bytes(), b"old workbook")
            self.assertFalse((target / "_archive").exists())
            self.assertFalse((target / "partial.crdownload").exists())

    def test_seed_never_overwrites_operator_files(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source, target = root / "seed", root / "work"
            (source / "data/derived").mkdir(parents=True)
            (source / "data/derived/a.csv").write_text("image")
            p.seed(target, source)
            (target / "data/derived/a.csv").write_text("operator")
            p.seed(target, source)
            self.assertEqual((target / "data/derived/a.csv").read_text(), "operator")

    def test_only_current_complete_success_can_publish(self):
        started = time.time()
        valid = dict(run_id="new", status="success", current_stage="finished",
                     started_at=datetime.fromtimestamp(started).isoformat(),
                     finished_at=datetime.now().isoformat(), sources_requested=list(p.SOURCES),
                     successes=list(p.SOURCES), failures=[], status_write_errors=[], error="",
                     marts_ok=True, processed_ok=True)
        p.require_success(valid, "old", started)
        cases = ({"status": "running"}, {"run_id": "old"}, {"successes": ["monitoring"]},
                 {"sources_requested": ["monitoring"]}, {"processed_ok": False},
                 {"marts_ok": False}, {"status_write_errors": ["disk full"]},
                 {"started_at": "2020-01-01T00:00:00"}, {"failures": ["fedstat"]})
        for changes in cases:
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                p.require_success({**valid, **changes}, "old", started)

    def test_failed_validation_does_not_switch_pointer(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            working, runtime = root / "work", root / "runtime"
            (runtime / "releases/old").mkdir(parents=True)
            try:
                (runtime / "live").symlink_to("releases/old", target_is_directory=True)
            except OSError:
                self.skipTest("Host does not allow directory symlinks")
            with patch.object(p, "validate", side_effect=RuntimeError("stale mart")):
                with self.assertRaises(RuntimeError):
                    p.publish(working, runtime)
            self.assertEqual(os.readlink(runtime / "live"), "releases/old")
            self.assertFalse((runtime / "pending.json").exists())
            self.assertEqual(list((runtime / "releases").iterdir()), [runtime / "releases/old"])
            with self.assertRaises(RuntimeError):
                p.swap(runtime, "releases/..")

    def test_candidate_uses_real_all_mart_loaders_and_detects_ns_mutation(self):
        from deploy.linux.tests import smoke
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            with patch.object(smoke, "ROOT", root):
                smoke.seed("PUBLICATION CONTRACT")
            # Approved static files used by the route smoke; no mutable local data.
            for relative in ("data/raw/realty/linear_objects/linear_objects_2026-09-28.json",
                             "data/raw/realty/vvod/static_vvod_rs_2011_2025.xlsx",
                             "data/raw/realty/vvod/emiss_34118_base.xls",
                             "data/derived/salary_2011_2012.csv",
                             "data/derived/vds_msk_value_2011_2015.csv"):
                destination = root / relative
                destination.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(REPO / relative, destination)
            environment = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"),
                           "PARSER_REQUIRE_REALTY_MARTS": "1", "PYTHONUTF8": "1"}
            result = subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"],
                                    cwd=REPO, env=environment, capture_output=True, text=True, encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            p.validate(root)
            raw = next((root / "data/raw/realty/nashdom").glob("*.xlsx"))
            stat = raw.stat()
            # Change only ns within the same CLI's microsecond timestamp bucket.
            os.utime(raw, ns=(stat.st_atime_ns, stat.st_mtime_ns - 100))
            with self.assertRaises(subprocess.CalledProcessError):
                p.validate(root)

    def test_rollback_keeps_recovery_journal_until_acknowledged(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / "releases/old").mkdir(parents=True)
            (runtime / "releases/new").mkdir()
            try:
                (runtime / "live").symlink_to("releases/new", target_is_directory=True)
            except OSError:
                self.skipTest("Host does not allow directory symlinks")
            journal = runtime / "pending.json"
            journal.write_text(json.dumps({"previous": "releases/old", "next": "releases/new"}))
            p.transaction(runtime, "rollback")
            self.assertEqual(p.live_target(runtime), "releases/old")
            self.assertTrue(journal.exists())
            p.transaction(runtime, "rollback")  # Recovery is safe to retry after interruption.
            p.transaction(runtime, "ack")
            self.assertFalse(journal.exists())


if __name__ == "__main__":
    unittest.main()
