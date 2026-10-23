"""Supplemental release retention and independent Docker update contracts."""
from contextlib import nullcontext
import json
import os
from pathlib import Path
import subprocess
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from docker import publication, preferential
from tests.test_preferential_downloader import document, parser_contract, RELATIVE
from tests.test_docker_recovery import BASH, REPO


class PreferentialPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.candidate, self.previous = self.root / "candidate", self.root / "previous"
        self.candidate.mkdir()
        self.previous.mkdir()
        self.contract = parser_contract()
        contract = patch.dict("sys.modules", {"pipeline.preferential_mortgage": self.contract})
        contract.start()
        self.addCleanup(contract.stop)

    def write(self, root, value=None):
        path = root / RELATIVE
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value or document()), encoding="utf-8")
        return path

    def test_missing_source_is_compatible_with_existing_releases(self):
        declaration = preferential.select_source(self.candidate, self.previous)
        self.assertEqual(declaration["state"], "unavailable")
        preferential.verify_snapshot(self.candidate, declaration)

    def test_current_source_is_verified_and_bound_to_hash_and_mtime(self):
        path = self.write(self.candidate)
        declaration = preferential.select_source(self.candidate, self.previous)
        self.assertEqual(declaration["state"], "available")
        preferential.verify_snapshot(self.candidate, declaration)
        stamp = path.stat().st_mtime_ns
        os.utime(path, ns=(stamp + 100, stamp + 100))
        with self.assertRaises(RuntimeError):
            preferential.verify_snapshot(self.candidate, declaration)
        os.utime(path, ns=(stamp, stamp))
        path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
        with self.assertRaises(RuntimeError):
            preferential.verify_snapshot(self.candidate, declaration)

    def test_invalid_missing_and_older_candidates_retain_previous_untouched(self):
        path = self.write(self.previous, document("2026-10-20", "2026-10-21T00:00:00Z"))
        before, stamp = path.read_bytes(), path.stat().st_mtime_ns
        for case in ("invalid", "missing", "older"):
            candidate = self.write(self.candidate)
            if case == "invalid":
                candidate.write_text("{invalid", encoding="utf-8")
            elif case == "missing":
                candidate.unlink()
            with self.subTest(case=case):
                declaration = preferential.select_source(self.candidate, self.previous)
                self.assertEqual(declaration["state"], "retained")
                self.assertEqual(candidate.read_bytes(), before)
                self.assertEqual(path.read_bytes(), before)
                self.assertEqual(path.stat().st_mtime_ns, stamp)
                preferential.verify_snapshot(self.candidate, declaration)

    def test_newer_source_date_wins_over_later_collection_of_old_workbook(self):
        self.write(self.previous, document("2026-10-01", "2026-10-24T00:00:00Z"))
        self.write(self.candidate, document("2026-10-11", "2026-10-12T00:00:00Z"))
        self.assertEqual(preferential.select_source(self.candidate, self.previous)["selection"], "current")

    def test_invalid_source_cleanup_is_limited_to_fixed_candidate_path(self):
        invalid = self.write(self.candidate, {"records": []})
        unrelated = invalid.parent / "other.json"
        unrelated.write_text("unrelated")
        self.assertEqual(preferential.select_source(self.candidate)["state"], "unavailable")
        self.assertFalse(invalid.exists())
        self.assertEqual(unrelated.read_text(), "unrelated")

    def test_bounded_collector_reports_success_retention_failure_and_timeout(self):
        for code, expected in ((0, "success"), (2, "retained"), (1, "failed")):
            def runner(command, **kwargs):
                self.assertIn("pipeline.downloaders.domrf_preferential", command)
                self.assertEqual(kwargs["cwd"], self.candidate)
                self.assertGreater(kwargs["timeout"], 0)
                return subprocess.CompletedProcess(command, code)
            self.assertEqual(preferential.collect(self.candidate, runner=runner)["status"], expected)
        for error in (subprocess.TimeoutExpired("collector", 1), OSError("cannot start")):
            def runner(*args, **kwargs):
                raise error
            self.assertEqual(preferential.collect(self.candidate, runner=runner)["status"], "failed")

    def test_daily_check_runs_before_core_and_does_not_block_core_on_failure(self):
        for core_failure in (False, True):
            for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
                (self.root / name).write_text("fixture")
            events = []
            with patch.object(publication, "seed"), \
                    patch.object(publication, "read_status", side_effect=[{"run_id": "old"}, {"run_id": "new"}]), \
                    patch.object(publication, "bootstrap_baseline", return_value=nullcontext("baseline")), \
                    patch("docker.mortgage.collect", side_effect=lambda _: events.append("mortgage")), \
                    patch.object(preferential, "collect", side_effect=lambda _: events.append("preferential") or {"status": "failed"}), \
                    patch.object(publication.subprocess, "run", side_effect=lambda *a, **kw: events.append("core") or subprocess.CompletedProcess(a[0], int(core_failure))), \
                    patch.object(publication, "require_success", side_effect=RuntimeError("core failed") if core_failure else None), \
                    patch.object(publication, "publish") as publish:
                if core_failure:
                    with self.assertRaisesRegex(RuntimeError, "core failed"):
                        publication.run_action("update", self.root, self.root / "runtime")
                    publish.assert_not_called()
                else:
                    publication.run_action("update", self.root, self.root / "runtime")
                    publish.assert_called_once()
            self.assertEqual(events, ["mortgage", "preferential", "core"])

    def test_publish_selects_newer_previous_source_and_writes_bound_declaration(self):
        runtime = self.root / "runtime"
        previous = runtime / "releases/old"
        path = self.write(previous, document("2026-10-20"))
        original = path.read_bytes()
        self.write(self.candidate, document("2026-10-11"))
        metadata = {"optional_sources": {}, "mode": "offline_import"}
        with patch.object(publication, "live_target", return_value="releases/old"), \
                patch.object(publication, "swap") as swap, \
                patch.object(publication, "select_candidate_erz", return_value={"selection": "none", "reason": "fixture"}), \
                patch.object(publication, "publication_status", return_value=metadata), \
                patch.object(publication, "build_candidate_processed"), \
                patch.object(publication, "build_candidate"), \
                patch.object(publication, "validate"):
            publication.publish(self.candidate, runtime)
        swap.assert_called_once()
        selected = runtime / swap.call_args.args[1]
        self.assertEqual((selected / RELATIVE).read_bytes(), original)
        self.assertEqual(path.read_bytes(), original)
        published = json.loads((selected / "data/processed/publication_status.json").read_text(encoding="utf-8"))
        declaration = published["supplemental_sources"]["preferential_mortgage"]
        self.assertEqual(declaration["state"], "retained")
        preferential.verify_snapshot(selected, declaration)

    def proof_files(self):
        for relative in ("data/marts/realty/manifest.json", "data/processed/publication_status.json"):
            path = self.candidate / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text('{"marts":{}}', encoding="utf-8")

    def test_manifest_proof_verifies_preferential_declaration_and_content(self):
        from pipeline import source_availability as availability
        path = self.write(self.candidate)
        declaration = preferential.select_source(self.candidate)
        self.proof_files()
        metadata = {"full_marts_built": True, "supplemental_sources": {"preferential_mortgage": declaration}}
        with patch.object(availability, "read_publication", return_value=metadata), \
                patch("pipeline.build_realty_marts._specs", return_value=[]):
            availability.verify_manifest(self.candidate)
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "preferential mortgage"):
                availability.verify_manifest(self.candidate)

    def test_preferential_mutation_or_missing_file_invalidates_cached_signature(self):
        from pipeline import source_availability as availability
        path = self.write(self.candidate)
        declaration = preferential.select_source(self.candidate)
        self.proof_files()
        with patch.object(availability, "read_publication", return_value={"supplemental_sources": {"preferential_mortgage": declaration}}), \
                patch("pipeline.build_realty_marts._specs", return_value=[]):
            before = availability.manifest_signature(self.candidate)
            path.write_text(path.read_text(encoding="utf-8") + " ", encoding="utf-8")
            self.assertNotEqual(availability.manifest_signature(self.candidate), before)
            path.unlink()
            with self.assertRaises(OSError):
                availability.manifest_signature(self.candidate)

    def test_invalid_or_unavailable_declaration_cannot_hide_a_raw_source(self):
        from pipeline import source_availability as availability
        self.write(self.candidate)
        self.proof_files()
        for declaration in ([], {"state": "unavailable", "file": None}):
            metadata = {"supplemental_sources": {"preferential_mortgage": declaration}}
            with self.subTest(declaration=declaration), \
                    patch.object(availability, "read_publication", return_value=metadata), \
                    patch("pipeline.build_realty_marts._specs", return_value=[]):
                with self.assertRaises(RuntimeError):
                    availability.verify_inventory(self.candidate, metadata)
                if isinstance(declaration, dict):
                    signature = availability.manifest_signature(self.candidate)
                    self.assertIn(str(self.candidate / RELATIVE), {row[0] for row in signature})
                else:
                    with self.assertRaises(RuntimeError):
                        availability.manifest_signature(self.candidate)


@unittest.skipUnless(BASH, "Dedicated update mode needs Bash, not Docker")
class PreferentialModeTests(unittest.TestCase):
    def run_mode(self, status):
        source = (REPO / "docker/update.sh").read_text(encoding="utf-8")
        self.assertIn('"$mode" == --preferential-mortgage', source.split("mkdir -p", 1)[0])
        start = source.index('event updater_start "mode=$mode"')
        branch = source[start:source.index("if ! run_updater;", start)]
        warning = source[source.index('if [[ "$preferential_retained" == 1 ]]; then'):]
        harness = '''set -euo pipefail
mode=--preferential-mortgage
event() { printf '%s %s\\n' "$1" "${2:-}"; }
control() { printf 'CONTROL:%s\\n' "$1"; }
docker() { printf 'DOCKER:%s\\n' "$*"; return "$COLLECT_TEST"; }
'''
        return subprocess.run([BASH, "-c", harness + branch + "\nrun_updater\n" + warning],
                              env={**os.environ, "COLLECT_TEST": str(status)}, capture_output=True, text=True, timeout=10)

    def test_success_collects_and_publishes_without_other_collectors(self):
        result = self.run_mode(0)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn("pipeline.downloaders.domrf_preferential", result.stdout)
        self.assertIn("CONTROL:publish", result.stdout)
        self.assertNotIn("scripts/update_realty.py", result.stdout)
        self.assertNotIn("pipeline.downloaders.nashdom_mortgage", result.stdout)

    def test_seed_or_retained_source_publishes_and_reports_warning_exit_two(self):
        result = self.run_mode(2)
        self.assertEqual(result.returncode, 2, result.stdout + result.stderr)
        self.assertIn("CONTROL:publish", result.stdout)
        self.assertIn("preferential_mortgage_retained", result.stdout)
        self.assertIn("live acquisition did not refresh", result.stdout)

    def test_failure_and_timeout_without_source_do_not_publish(self):
        for code in (1, 124):
            result = self.run_mode(code)
            self.assertEqual(result.returncode, code, result.stdout + result.stderr)
            self.assertNotIn("CONTROL:publish", result.stdout)


if __name__ == "__main__":
    unittest.main()
