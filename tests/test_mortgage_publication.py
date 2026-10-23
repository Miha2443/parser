"""Mortgage onboarding must not invalidate the existing eight-source release."""
import json
from contextlib import nullcontext
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.tests.test_mortgage import observation, source
from docker.mortgage import RELATIVE, collect, select_source, verify_snapshot
from docker.publication import copy_tree
from docker import publication


class MortgagePublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candidate = self.root / "candidate"
        self.previous = self.root / "previous"
        self.candidate.mkdir()
        self.previous.mkdir()

    def write(self, root, document=None):
        document = source([observation("2026-01", region=region) for region in ("rf", "msk")]) if document is None else document
        path = root / RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")
        return path

    def daily_update(self, *, core_failure=False, mortgage_status="success"):
        for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
            (self.root / name).write_bytes(b"isolated test source fingerprint")
        events = []
        old, new = {"run_id": "old"}, {"run_id": "new"}
        with patch.object(publication, "seed"), \
                patch.object(publication, "read_status", side_effect=[old, new]), \
                patch.object(publication, "requested_sources", return_value=publication.SOURCES), \
                patch.object(publication, "bootstrap_baseline", return_value=nullcontext("baseline")), \
                patch("docker.mortgage.collect", side_effect=lambda root: events.append("mortgage") or {"status": mortgage_status}) as mortgage, \
                patch("docker.preferential.collect", return_value={"status": "failed"}), \
                patch.object(publication.subprocess, "run", side_effect=lambda *args, **kwargs:
                             events.append("core") or subprocess.CompletedProcess(args[0], int(core_failure))), \
                patch.object(publication, "require_success", side_effect=RuntimeError("core failed") if core_failure else None), \
                patch.object(publication, "publish") as publish:
            if core_failure:
                with self.assertRaisesRegex(RuntimeError, "core failed"):
                    publication.run_action("update", self.root, self.root / "runtime")
                publish.assert_not_called()
            else:
                publication.run_action("update", self.root, self.root / "runtime")
                publish.assert_called_once_with(self.root, self.root / "runtime", run_status=new,
                                               collector_returncode=0, baseline="baseline")
            mortgage.assert_called_once_with(self.root)
        self.assertEqual(events, ["mortgage", "core"])

    def test_daily_mortgage_check_runs_even_if_a_core_provider_fails(self):
        self.daily_update(core_failure=True)

    def test_daily_mortgage_failure_does_not_block_healthy_core_publication(self):
        self.daily_update(mortgage_status="failed")

    def test_first_release_without_mortgage_is_compatible(self):
        result = select_source(self.candidate, self.previous)
        self.assertEqual(result["state"], "unavailable")
        verify_snapshot(self.candidate, result)

    def test_valid_history_is_selected_and_bound_to_its_hash(self):
        path = self.write(self.candidate)
        result = select_source(self.candidate, self.previous)
        self.assertEqual(result["state"], "available")
        self.assertEqual(result["file"], RELATIVE.as_posix())
        verify_snapshot(self.candidate, result)
        changed = json.loads(path.read_text(encoding="utf-8"))
        changed["records"][0]["volumeRub"] += 1
        path.write_text(json.dumps(changed), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            verify_snapshot(self.candidate, result)

    def test_invalid_new_history_retains_verified_old_without_mutating_old(self):
        previous = self.write(self.previous)
        before, stamp = previous.read_bytes(), previous.stat().st_mtime_ns
        current = self.write(self.candidate)
        current.write_text("{broken", encoding="utf-8")
        result = select_source(self.candidate, self.previous)
        self.assertEqual(result["state"], "retained")
        self.assertEqual(current.read_bytes(), before)
        self.assertEqual(previous.read_bytes(), before)
        self.assertEqual(previous.stat().st_mtime_ns, stamp)
        verify_snapshot(self.candidate, result)

    def test_missing_region_or_only_null_observations_are_not_complete_history(self):
        document = source([observation("2026-01", region="rf")])
        self.write(self.candidate, document)
        self.assertEqual(select_source(self.candidate)["state"], "unavailable")
        document["records"].append(observation("2026-01", region="msk", volume=None, count=None))
        self.write(self.candidate, document)
        self.assertEqual(select_source(self.candidate)["state"], "unavailable")

    def test_invalid_optional_source_cannot_erase_other_sources(self):
        monitoring = self.candidate / "data/raw/realty/nashdom/monitoring_2_0.xlsx"
        monitoring.parent.mkdir(parents=True, exist_ok=True)
        monitoring.write_bytes(b"unrelated source")
        self.write(self.candidate, source([observation("2026-01", region="rf")]))
        result = select_source(self.candidate)
        self.assertEqual(result["state"], "unavailable")
        self.assertEqual(monitoring.read_bytes(), b"unrelated source")
        self.assertFalse((self.candidate / RELATIVE).exists())

    def test_private_resume_files_are_not_copied_into_runtime(self):
        raw = self.candidate / RELATIVE.parent
        (raw / ".mortgage_history").mkdir(parents=True)
        (raw / ".mortgage_history/partial.json").write_text("private checkpoint")
        self.write(self.candidate)
        copy_tree(self.candidate, self.previous)
        self.assertTrue((self.previous / RELATIVE).is_file())
        self.assertFalse((self.previous / RELATIVE.parent / ".mortgage_history").exists())

    def test_collection_partial_and_failure_are_explicit_without_core_failure(self):
        calls = []
        def runner(command, **kwargs):
            calls.append((command, kwargs))
            return subprocess.CompletedProcess(command, 2)
        result = collect(self.candidate, runner=runner)
        self.assertEqual(result["status"], "partial")
        self.assertEqual(result["returncode"], 2)
        self.assertIn("pipeline.downloaders.nashdom_mortgage", calls[0][0])
        self.assertEqual(calls[0][1]["cwd"], self.candidate)
        for error in (subprocess.TimeoutExpired("collector", 1), OSError("cannot start")):
            with self.subTest(error=type(error).__name__):
                def fail(*args, **kwargs):
                    raise error
                self.assertEqual(collect(self.candidate, runner=fail)["status"], "failed")

    def test_mortgage_file_changes_invalidate_the_health_proof_signature(self):
        from pipeline.source_availability import manifest_signature
        path = self.write(self.candidate)
        declaration = select_source(self.candidate)
        for relative in ("data/marts/realty/manifest.json", "data/processed/publication_status.json"):
            target = self.candidate / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text('{"marts": {}}', encoding="utf-8")
        with patch("pipeline.source_availability.read_publication", return_value={"supplemental_sources": {"mortgage": declaration}}), \
                patch("pipeline.build_realty_marts._specs", return_value=[]):
            before = manifest_signature(self.candidate)
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            self.assertNotEqual(manifest_signature(self.candidate), before)
            path.unlink()
            with self.assertRaises(OSError):
                manifest_signature(self.candidate)


if __name__ == "__main__":
    unittest.main()
