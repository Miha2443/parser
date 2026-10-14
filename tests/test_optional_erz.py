"""Optional ERZ never weakens the six-source or publication contracts."""
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
from docker.coverage import REQUIRED_SOURCES, SOURCES, validate_erz_sources, validate_sources, validate_pages
from pipeline.data_access import DataAccess, DataContext

REPO = Path(__file__).resolve().parents[1]


class OptionalErzTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deploy.linux.tests import smoke
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.seed = Path(cls.temp.name) / "seed"
        with patch.object(smoke, "ROOT", cls.seed):
            smoke.seed("OPTIONAL ERZ TEST")

    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))
        self.root = self.directory / "root"
        shutil.copytree(self.seed, self.root)

    def access(self):
        return DataAccess(DataContext(self.root, downloads=self.root / "downloads", use_marts=True, require_marts=True))

    def no_erz(self):
        for file in (self.root / "data/raw/realty/erzrf").iterdir():
            file.unlink()

    def build(self, run=subprocess.run):
        result = run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"], cwd=REPO,
                     env={**os.environ, "PARSER_ROOT": str(self.root), "PARSER_DOWNLOADS": str(self.root / "downloads")},
                     capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        return result

    def status(self, requested=REQUIRED_SOURCES, failures=()):
        return dict(run_id="fresh", status="failed" if failures else "success", current_stage="finished",
                    started_at=datetime.now().isoformat(), finished_at=datetime.now().isoformat(),
                    sources_requested=sorted(requested), successes=sorted(set(requested) - set(failures)),
                    failures=list(failures), error="source failures: " + ", ".join(failures) if failures else "",
                    status_write_errors=[], processed_ok=True, marts_ok=True)

    def raw_access(self, root):
        return DataAccess(DataContext(root, downloads=root / "downloads", use_marts=False))

    def erz_bytes(self, root):
        directory = root / "data/raw/realty/erzrf"
        return {str(file.relative_to(directory)): (file.read_bytes(), file.stat().st_mtime_ns)
                for file in directory.rglob("*") if file.is_file()}

    def previous_release(self, runtime):
        # Synthetic export evidence also covers TOP sidecars, which are not
        # required by the loader but must remain part of the immutable cohort.
        from pipeline.source_records import file_sha256
        top = next((self.root / "data/raw/realty/erzrf").glob("top_*.xlsx"))
        top.with_suffix(".xlsx.provenance.json").write_text(json.dumps({
            "schema_version": 1, "sha256": file_sha256(top), "verified": True,
            "selection": {"region": "rf", "sorting": "obyem_stroitelstva"}}))
        previous = runtime / "releases/old"
        shutil.copytree(self.root, previous)
        return previous

    def mismatched_targets(self):
        target = next((self.root / "data/raw/realty/erzrf").glob("top_developers_rf_*.json"))
        payload = json.loads(target.read_text(encoding="utf-8"))
        payload["scraped_at"] = "2026-10-14T12:00:00"
        target.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        # Card identity/coverage gates remain strict on the working input.
        with self.assertRaisesRegex(RuntimeError, "verified target coverage"):
            validate_erz_sources(self.raw_access(self.root))

    def published(self, runtime):
        release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        return release, json.loads((release / "data/processed/publication_status.json").read_text())

    def test_request_scope_and_failures_are_exact_and_only_erz_can_degrade(self):
        for enabled, requested, failures in (("0", REQUIRED_SOURCES, ()), ("1", SOURCES, ()),
                                             ("1", SOURCES, ("erz-top", "erz-cards"))):
            with patch.dict(os.environ, {"UPDATE_ERZ": enabled}):
                valid = self.status(requested, failures)
                p.require_success(valid, None, time.time(), returncode=2 if failures else 0)
                for changes in ({"successes": ["monitoring"]}, {"sources_requested": sorted(requested) + ["monitoring"]},
                                {"processed_ok": False}, {"marts_ok": False}, {"error": "unexpected crash"},
                                {"current_stage": "running"}, {"run_id": "previous"}):
                    with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                        p.require_success({**valid, **changes}, "previous", time.time())
        with patch.dict(os.environ, {"UPDATE_ERZ": "1"}), self.assertRaises(RuntimeError):
            p.require_success(self.status(SOURCES, ("fedstat",)), None, time.time(), returncode=2)
        with patch.dict(os.environ, {"UPDATE_ERZ": "0"}), self.assertRaises(RuntimeError):
            p.require_success(self.status(SOURCES), None, time.time())

    def test_no_erz_first_snapshot_has_all_marts_pages_and_null_erz_facts(self):
        from fastapi.testclient import TestClient
        from backend.app import create_app
        self.no_erz()
        self.assertFalse((self.root / "data/marts/realty/manifest.json").exists())
        self.build()
        validate_sources(self.access())
        with patch.dict(os.environ, {"PARSER_ROOT": str(self.root), "PARSER_DOWNLOADS": str(self.root / "downloads"),
                                     "PARSER_REQUIRE_REALTY_MARTS": "1"}), TestClient(create_app()) as client:
            def get(route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200, route)
                return response.json()
            validate_pages(get)
            catalog = get("/api/v1/catalog")
            selected = catalog["controls"]["developers"][0]
            profile = get("/api/v1/profile?developer=" + selected["developerKey"] + "&region=msk")["profiles"][0]
            self.assertTrue(all(row["valueM2"] is None for row in profile["delays"]["cards"]))
            self.assertEqual(catalog["provenance"]["sources"]["erzrf_top"]["availability"], "unavailable")
        runtime = self.directory / "runtime"
        with patch.object(p, "swap"):
            p.publish(self.root, runtime)  # Real candidate/check/API gates, no Windows symlink privilege required.
        self.assertTrue((runtime / "pending.json").is_file())

    def test_missing_mandatory_or_malformed_present_erz_still_blocks(self):
        self.no_erz()
        self.build()
        next((self.root / "data/raw/realty/nashdom").glob("construction_operational_*.json")).unlink()
        self.build()
        with self.assertRaisesRegex(RuntimeError, "construction"):
            validate_sources(self.access())
        shutil.copytree(self.seed, self.root, dirs_exist_ok=True)
        workbook = next((self.root / "data/raw/realty/erzrf").glob("cards_*.xlsx"))
        workbook.with_suffix(".xlsx.provenance.json").unlink()
        self.build()
        with self.assertRaisesRegex(RuntimeError, "erz-cards"):
            validate_sources(self.access())

    def test_absent_erz_cannot_hide_fabricated_historical_frames_or_developers(self):
        import pandas as pd
        self.no_erz()
        self.build()
        path = self.root / "data/marts/realty/erzrf_top.pkl"
        original = pd.read_pickle(path)
        for field in ("history", "names"):
            payload = dict(original)
            if field == "history":
                payload["obyem_vvoda_by_year"] = {"rf": {2025: pd.DataFrame([{"Введено, м²": 1000}])}}
            else:
                payload["all_developers"] = ["Invented"]
            pd.to_pickle(payload, path)
            with self.subTest(field=field), self.assertRaisesRegex(RuntimeError, "without source files"):
                validate_sources(self.access())

    def test_first_runner_skips_erz_and_builds_all_nine_before_publication(self):
        self.no_erz()
        for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
            (self.root / name).write_text("test stub")
        calls, original = [], subprocess.run
        runtime = self.directory / "runtime"
        def run(command, **kwargs):
            calls.append(command)
            if "scripts/update_realty.py" in command:
                self.assertIn("--no-marts", command)
                self.assertIn("--skip-optional-processed", command)
                self.assertNotIn("erz-top", command)
                self.assertNotIn("erz-cards", command)
                (self.root / "data/processed/realty_update_status.json").write_text(json.dumps(self.status()))
                return subprocess.CompletedProcess(command, 0)
            return original(command, **kwargs)
        with patch.dict(os.environ, {"UPDATE_ERZ": "0"}), patch.object(p.subprocess, "run", side_effect=run), patch.object(p, "swap"):
            p.run_action("update", self.root, runtime)
        next_release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        metadata = json.loads((next_release / "data/processed/publication_status.json").read_text())
        self.assertTrue(metadata["full_marts_built"])
        self.assertEqual(metadata["optional_sources"]["erz-top"]["state"], "unavailable")
        self.assertEqual(len(calls), 4)  # Collector, candidate build, manifest check, API check.
        self.assertFalse((self.root / "data/marts/realty/manifest.json").exists())

    def test_failed_erz_keeps_existing_measurements_and_dates(self):
        self.build()
        files = list((self.root / "data/raw/realty/erzrf").iterdir())
        before = {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in files}
        status = self.status(SOURCES, ("erz-top", "erz-cards"))
        p.require_success(status, None, time.time(), expected_sources=SOURCES, returncode=2)
        metadata = p.publication_status(self.root, status, 2, full_marts_built=True)
        self.assertEqual(metadata["status"], "degraded")
        self.assertEqual(metadata["collector_status"], "failed")
        self.assertTrue(all(item["state"] == "retained" for item in metadata["optional_sources"].values()))
        validate_sources(self.access())
        self.assertEqual(before, {file.name: (file.read_bytes(), file.stat().st_mtime_ns) for file in files})

    def test_runner_admits_erz_failure_but_rejects_mandatory_failure_before_publication(self):
        for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
            (self.root / name).write_text("test stub")
        original = subprocess.run
        for failures in (("erz-top", "erz-cards"), ("fedstat",)):
            (self.root / "data/processed/realty_update_status.json").unlink(missing_ok=True)
            runtime = self.directory / ("runtime-" + failures[0])
            def run(command, **kwargs):
                if "scripts/update_realty.py" in command:
                    self.assertIn("erz-top", command)
                    self.assertIn("--skip-optional-processed", command)
                    (self.root / "data/processed/realty_update_status.json").write_text(json.dumps(self.status(SOURCES, failures)))
                    return subprocess.CompletedProcess(command, 2)
                return original(command, **kwargs)
            with patch.dict(os.environ, {"UPDATE_ERZ": "1"}), patch.object(p.subprocess, "run", side_effect=run), patch.object(p, "swap"):
                if failures == ("fedstat",):
                    with self.assertRaises(RuntimeError):
                        p.run_action("update", self.root, runtime)
                    self.assertFalse((runtime / "pending.json").exists())
                else:
                    p.run_action("update", self.root, runtime)
                    next_release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
                    info = json.loads((next_release / "data/processed/publication_status.json").read_text())
                    self.assertEqual(info["collector_returncode"], 2)
                    self.assertEqual(info["collector_status"], "failed")
                    self.assertTrue(info["full_marts_built"])

    def test_new_top_old_cards_uses_previous_cohort_with_identical_bytes_dates_and_top_evidence(self):
        runtime = self.directory / "runtime"
        previous = self.previous_release(runtime)
        old = self.erz_bytes(previous)
        self.mismatched_targets()
        archive = self.root / "data/raw/realty/erzrf/_archive"
        archive.mkdir()
        (archive / "cards_old.xlsx").write_bytes(b"recoverable history")
        working = self.erz_bytes(self.root)
        status = self.status(SOURCES, ("erz-cards",))
        with patch.object(p, "live_target", return_value="releases/old"), patch.object(p, "swap"):
            p.publish(self.root, runtime, run_status=status, collector_returncode=2)
        release, metadata = self.published(runtime)
        self.assertEqual(metadata["erz_selection"]["selection"], "previous")
        self.assertIn("verified target coverage", metadata["erz_selection"]["reason"])
        self.assertEqual(metadata["status"], "degraded")
        self.assertTrue(all(row["state"] == "retained" for row in metadata["optional_sources"].values()))
        self.assertEqual(self.erz_bytes(release), old)
        self.assertEqual(self.erz_bytes(previous), old)
        self.assertEqual(self.erz_bytes(self.root), working)
        for row in metadata["optional_sources"].values():
            for name, date in row["source_dates"].items():
                self.assertAlmostEqual(datetime.fromisoformat(date).timestamp(),
                                       (previous / "data/raw/realty/erzrf" / name).stat().st_mtime, delta=1e-6)

    def test_offline_incompatible_or_corrupt_erz_without_previous_publishes_six_and_keeps_working_inputs(self):
        for defect in ("new_targets", "corrupt_excel"):
            with self.subTest(defect=defect):
                shutil.copytree(self.seed, self.root, dirs_exist_ok=True)
                if defect == "new_targets":
                    self.mismatched_targets()
                else:
                    next((self.root / "data/raw/realty/erzrf").glob("cards_*.xlsx")).write_bytes(b"<html>provider error</html>")
                before = self.erz_bytes(self.root)
                runtime = self.directory / defect
                with patch.object(p, "swap"):
                    p.run_action("publish", self.root, runtime)
                release, metadata = self.published(runtime)
                self.assertEqual(metadata["erz_selection"]["selection"], "none")
                self.assertIn("Current ERZ rejected", metadata["erz_selection"]["reason"])
                self.assertTrue(all(row["state"] == "unavailable" for row in metadata["optional_sources"].values()))
                self.assertEqual(self.erz_bytes(release), {})
                self.assertEqual(self.erz_bytes(self.root), before)

    def test_disabled_erz_next_update_still_repairs_incoherent_working_cohort_without_collecting_it(self):
        runtime = self.directory / "runtime"
        previous = self.previous_release(runtime)
        self.mismatched_targets()
        old, working = self.erz_bytes(previous), self.erz_bytes(self.root)
        for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
            (self.root / name).write_text("test stub")
        original = subprocess.run
        def run(command, **kwargs):
            if "scripts/update_realty.py" in command:
                self.assertNotIn("erz-top", command)
                self.assertNotIn("erz-cards", command)
                (self.root / "data/processed/realty_update_status.json").write_text(json.dumps(self.status()))
                return subprocess.CompletedProcess(command, 0)
            return original(command, **kwargs)
        with patch.dict(os.environ, {"UPDATE_ERZ": "0"}), patch.object(p.subprocess, "run", side_effect=run), \
                patch.object(p, "live_target", return_value="releases/old"), patch.object(p, "swap"):
            p.run_action("update", self.root, runtime)
        release, metadata = self.published(runtime)
        self.assertEqual(metadata["erz_selection"]["selection"], "previous")
        self.assertEqual(self.erz_bytes(release), old)
        self.assertEqual(self.erz_bytes(self.root), working)

    def test_erz_fallback_cannot_hide_missing_required_source_or_mutate_previous(self):
        runtime = self.directory / "runtime"
        previous = self.previous_release(runtime)
        old = self.erz_bytes(previous)
        self.mismatched_targets()
        working = self.erz_bytes(self.root)
        next((self.root / "data/raw/realty/nashdom").glob("construction_operational_*.json")).unlink()
        with patch.object(p, "live_target", return_value="releases/old"), patch.object(p, "swap") as swap:
            with self.assertRaises(subprocess.CalledProcessError):
                p.publish(self.root, runtime)
        swap.assert_not_called()
        self.assertFalse((runtime / "pending.json").exists())
        self.assertEqual(self.erz_bytes(previous), old)
        self.assertEqual(self.erz_bytes(self.root), working)
        self.assertEqual(list((runtime / "releases").iterdir()), [previous])

    def test_offline_processed_command_is_bounded_to_required_sources_and_propagates_failure(self):
        from scripts.update_realty import select_processed_indicators
        for failure in (False, True):
            with self.subTest(failure=failure), patch.object(p.subprocess, "run") as run:
                if failure:
                    run.side_effect = subprocess.CalledProcessError(2, ["processed"])
                    with self.assertRaises(subprocess.CalledProcessError):
                        p.run_action("build-required-processed", self.root, self.directory / "runtime")
                else:
                    p.run_action("build-required-processed", self.root, self.directory / "runtime")
                command = run.call_args.args[0]
                self.assertIn("--skip-download", command)
                self.assertEqual(set(command[command.index("--only") + 1:]),
                                 select_processed_indicators(sorted(REQUIRED_SOURCES)))
                self.assertTrue(run.call_args.kwargs["check"])

    def test_invalid_previous_raw_cohort_is_not_trusted_and_neither_original_is_mutated(self):
        runtime = self.directory / "runtime"
        previous = self.previous_release(runtime)
        next((previous / "data/raw/realty/erzrf").glob("cards_*.xlsx.provenance.json")).unlink()
        self.mismatched_targets()
        candidate = self.directory / "candidate"
        shutil.copytree(self.root, candidate)
        old, working = self.erz_bytes(previous), self.erz_bytes(self.root)
        result = p.select_candidate_erz(candidate, previous)
        self.assertEqual(result["selection"], "none")
        self.assertIn("previous ERZ rejected", result["reason"])
        self.assertEqual(self.erz_bytes(candidate), {})
        validate_erz_sources(self.raw_access(candidate))
        self.assertEqual(self.erz_bytes(previous), old)
        self.assertEqual(self.erz_bytes(self.root), working)


if __name__ == "__main__":
    unittest.main()
