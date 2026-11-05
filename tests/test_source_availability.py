"""Provider evidence, family isolation and real private publication regressions."""
from datetime import datetime, timedelta, timezone
import copy
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from pipeline import source_availability as sa
from docker import publication as p


def run_status(skipped=(), failed=()):
    started = datetime.now(timezone.utc).isoformat()
    rows = {alias: sa.outcome(alias, "test-run", "success") for alias in sa.SOURCES}
    for alias in skipped:
        url = sa.URLS[alias][0]
        rows[alias] = sa.check_provider(alias, "test-run", check=lambda url: {
            "url": url, "checked_at": sa.stamp(), "http_status": 503, "text": "Service unavailable"})
    for alias in failed:
        rows[alias] = sa.outcome(alias, "test-run", "failed", reason_code="collector_exit_2", reason="Collector failed")
    return {"run_id": "test-run", "status": "failed" if failed else "degraded" if skipped else "success", "current_stage": "finished",
            "started_at": started, "finished_at": sa.stamp(), "sources_requested": sorted(sa.SOURCES),
            "successes": sorted(sa.SOURCES - set(skipped) - set(failed)), "failures": list(failed), "skipped": list(skipped),
            "source_outcomes": rows, "processed_ok": True, "marts_ok": True,
            "error": "source failures: " + ", ".join(failed) if failed else "", "status_write_errors": []}


class EvidenceTests(unittest.TestCase):
    def test_partial_publication_accepts_completed_source_failures_but_not_system_or_status_errors(self):
        valid = run_status(failed=["construction", "kvart", "rasprod", "rosstat"])
        started = sa.timestamp(valid["started_at"])
        p.require_success(valid, None, started, returncode=2, allow_partial=True)
        with self.assertRaises(RuntimeError):
            p.require_success(valid, None, started, returncode=2)
        for changes in ({"status": "running"}, {"current_stage": "collecting"}, {"finished_at": None},
                        {"processed_ok": False}, {"marts_ok": False}, {"status_write_errors": ["disk full"]},
                        {"error": valid["error"] + "; processed dashboard build failed"},
                        {"successes": valid["successes"] + ["construction"]}, {"source_outcomes": {}},
                        {"sources_requested": ["monitoring"]}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                p.require_success({**valid, **changes}, None, started, returncode=2, allow_partial=True)
        for code in (0, 1, 130, -9):
            with self.subTest(returncode=code), self.assertRaises(RuntimeError):
                p.require_success(valid, None, started, returncode=code, allow_partial=True)
        for invalid in ("stale", "wrong_alias", "partition"):
            current = copy.deepcopy(valid)
            row = current["source_outcomes"]["construction"]
            row.update({"checked_at": "2020-01-01T00:00:00+00:00"} if invalid == "stale" else
                       {"alias": "kvart"} if invalid == "wrong_alias" else {"status": "success"})
            with self.subTest(invalid=invalid), self.assertRaises(RuntimeError):
                p.require_success(current, None, started, returncode=2, allow_partial=True)

    def test_collector_failure_and_watchdog_are_failed_not_provider_outages(self):
        from scripts import update_realty as ur
        with patch.object(sa, "check_provider", return_value=None), patch.object(ur, "_collect_source") as collect:
            def watchdog(*args, **kwargs):
                kwargs["failure_info"].update(reason_code="watchdog_timeout", reason="Timed out", phase="watchdog")
                return False
            collect.side_effect = watchdog
            row = ur.run_source("fedstat", {"REALTY_RUN_ID": "run"})
            self.assertEqual((row["status"], row["phase"], row["reason_code"]), ("failed", "watchdog", "watchdog_timeout"))
        skipped = run_status(["fedstat"])["source_outcomes"]["fedstat"]
        with patch.object(sa, "check_provider", return_value=skipped), patch.object(ur, "_collect_source") as collect:
            self.assertEqual(ur.run_source("fedstat", {"REALTY_RUN_ID": "test-run"})["status"], "skipped")
            collect.assert_not_called()
        with patch.object(sa, "check_provider", side_effect=[None, skipped]) as checks, patch.object(ur, "_collect_source", return_value=False):
            self.assertEqual(ur.run_source("fedstat", {"REALTY_RUN_ID": "test-run"})["status"], "skipped")
            self.assertEqual(checks.call_count, 2)
        def timed_out(*args, **kwargs):
            kwargs["failure_info"].update(reason_code="watchdog_timeout", phase="watchdog", reason="Timed out")
            return False
        with patch.object(sa, "check_provider", side_effect=[None, copy.deepcopy(skipped)]), patch.object(ur, "_collect_source", side_effect=timed_out):
            row = ur.run_source("fedstat", {"REALTY_RUN_ID": "test-run"})
            self.assertEqual((row["status"], row["phase"], row["collector_reason_code"]), ("skipped", "postflight", "watchdog_timeout"))
        for code in ("backup_failed", "cancelled", "process_launch_failed", "data_or_authentication_error", "process_killed"):
            def failure(*args, **kwargs):
                kwargs["failure_info"].update(reason_code=code, reason=code)
                return False
            with self.subTest(code=code), patch.object(sa, "check_provider", return_value=None) as checks, patch.object(ur, "_collect_source", side_effect=failure):
                self.assertEqual(ur.run_source("fedstat", {"REALTY_RUN_ID": "run"})["status"], "failed")
                self.assertEqual(checks.call_count, 1)

    def test_real_failed_subprocess_rechecks_outage_without_masking_auth_or_memory(self):
        from scripts import update_realty as ur
        with tempfile.TemporaryDirectory() as temporary, patch.object(ur, "_print"):
            script = Path(temporary) / "failed_collector.py"
            env = {**os.environ, "REALTY_RUN_ID": "test-run"}
            for message in ("", "download failed", "JSONDecodeError: HTML instead of JSON", "BadZipFile: HTML instead of Excel", "incomplete coverage failed"):
                script.write_text("import sys\nprint(" + repr(message) + ")\nsys.exit(2)\n", encoding="utf-8")
                with self.subTest(message=message), patch.dict(ur.SOURCE_MAP, {"fedstat": (str(script), [])}):
                    with patch.object(sa, "check_provider", return_value=None) as checks:
                        row = ur.run_source("fedstat", env, do_archive=False)
                        self.assertEqual(row["status"], "failed")
                        self.assertEqual(row["reason_code"], "collector_exit_2")
                        self.assertEqual(checks.call_count, 2)
                    skipped = run_status(["fedstat"])["source_outcomes"]["fedstat"]
                    with patch.object(sa, "check_provider", side_effect=[None, skipped]):
                        row = ur.run_source("fedstat", env, do_archive=False)
                        self.assertEqual((row["status"], row["phase"]), ("skipped", "postflight"))
                        self.assertEqual(row["collector_reason_code"], "collector_exit_2")
            for message in ("invalid credentials", "MemoryError"):
                script.write_text("import sys\nprint(" + repr(message) + ")\nsys.exit(2)\n", encoding="utf-8")
                with self.subTest(message=message), patch.dict(ur.SOURCE_MAP, {"fedstat": (str(script), [])}), patch.object(sa, "check_provider", return_value=None) as checks:
                    row = ur.run_source("fedstat", env, do_archive=False)
                    self.assertEqual(row["reason_code"], "authentication_or_memory_error")
                    self.assertEqual(checks.call_count, 1)

    def test_only_two_confirmed_official_checks_can_skip(self):
        for status, text, expected in ((500, "", True), (503, "", True), (200, "Ведутся технические работы. Сайт временно недоступен", True),
                                      (403, "Forbidden", False), (200, "Новость: завтра ведутся технические работы", False),
                                      (200, "Login / password", False)):
            with self.subTest(status=status, text=text):
                row = sa.check_provider("fedstat", "r", check=lambda url: {"url": url, "checked_at": sa.stamp(), "http_status": status, "text": text})
                self.assertEqual(row is not None, expected)
        result = sa.check_provider("fedstat", "r", check=lambda url: {"url": url, "checked_at": sa.stamp(), "network_error": "read_timeout"})
        self.assertEqual(result["reason_code"], "provider_unreachable")
        answers = iter([503, 200] * len(sa.URLS["fedstat"]))
        self.assertIsNone(sa.check_provider("fedstat", "r", check=lambda url: {"url": url, "http_status": next(answers)}))

    def test_http_200_maintenance_heading_after_branding_is_not_a_future_news_item(self):
        class Response:
            status_code = 200
            url = sa.URLS["erz-top"][0]
            headers = {"Content-Type": "text/html; charset=utf-8"}
            def __init__(self, html): self.html = html
            def __enter__(self): return self
            def __exit__(self, *args): pass
            def iter_content(self, size): yield self.html.encode("utf-8")
        for heading, expected in (("Ведутся технические <span>работы</span>", "provider_maintenance"),
                                  ("Технические работы", "provider_maintenance"),
                                  ("Завтра ведутся технические работы", None),
                                  ("Новости", None)):
            html = "<header>" + "Логотип и навигация " * 100 + "</header><h1>" + heading + "</h1><p>Объявление: завтра ведутся технические работы</p>"
            with self.subTest(heading=heading):
                evidence = sa.http_check(Response.url, get=lambda *args, **kwargs: Response(html))
                self.assertEqual(sa.classify_check(evidence), expected)

    def test_stale_forged_wrong_alias_and_parser_failure_cannot_authorize_publication(self):
        valid = run_status(["fedstat"])
        p.require_success(valid, None, sa.timestamp(valid["started_at"]), returncode=0)
        for mutation in ("stale", "unofficial", "invented", "parser_error", "other_alias"):
            status = copy.deepcopy(valid)
            row = status["source_outcomes"]["fedstat"]
            if mutation == "stale":
                row["evidence"][0]["checked_at"] = "2020-01-01T00:00:00+00:00"
            elif mutation == "unofficial":
                for check in row["evidence"]:
                    check["url"] = "https://example.com/"
            elif mutation == "invented":
                for check in row["evidence"]:
                    check["http_status"] = 200
                    check["text"] = "Login"
            elif mutation == "other_alias":
                row["alias"] = "rosstat"
            else:
                row["status"] = "failed"
                status.update(status="failed", failures=["fedstat"], skipped=[], error="source failures: fedstat")
            with self.subTest(mutation=mutation), self.assertRaises(RuntimeError):
                p.require_success(status, None, sa.timestamp(valid["started_at"]), returncode=0)


class PublicationAvailabilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deploy.linux.tests import smoke
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.seed = Path(cls.temp.name) / "seed"
        with patch.object(smoke, "ROOT", cls.seed):
            smoke.seed("OUTAGE FIXTURE")

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.base = Path(self.temp)
        self.root = self.base / "work"
        shutil.copytree(self.seed, self.root)

    def inventory(self, root, alias):
        return {str(file.relative_to(root)): (file.read_bytes(), file.stat().st_mtime_ns) for file in sa.source_files(root, alias)}

    def test_each_outage_retains_one_validated_family_without_touching_neighbours_or_originals(self):
        previous = self.base / "previous"
        shutil.copytree(self.seed, previous)
        for alias in sorted(sa.SOURCES):
            with self.subTest(alias=alias):
                candidate = self.base / ("candidate-" + alias)
                shutil.copytree(self.seed, candidate)
                old = {key: self.inventory(previous, key) for key in sa.SOURCES}
                files = sa.source_files(candidate, alias)
                files[0].write_bytes(b"partial corrupt download")
                if alias in {"erz-top", "erz-cards"}:
                    unchanged = sa.SOURCES - {"erz-top", "erz-cards"}
                else:
                    unchanged = sa.SOURCES - {alias}
                neighbours = {key: self.inventory(candidate, key) for key in unchanged}
                metadata = p.select_candidate_sources(candidate, run_status([alias]), previous)
                self.assertEqual(metadata["sources"][alias]["state"], "retained")
                self.assertEqual(self.inventory(candidate, alias), old[alias])
                for key in unchanged:
                    self.assertEqual(self.inventory(candidate, key), neighbours[key])
                self.assertEqual({key: self.inventory(previous, key) for key in sa.SOURCES}, old)

    def test_each_failed_collector_retains_its_family_and_never_publishes_partial_files(self):
        previous = self.base / "previous"
        shutil.copytree(self.seed, previous)
        for alias in sorted(sa.SOURCES):
            with self.subTest(alias=alias):
                candidate = self.base / ("failed-" + alias)
                shutil.copytree(self.seed, candidate)
                failed_files = sa.source_files(candidate, alias)
                failed_files[0].write_bytes(b"partial failed source")
                paired = {"erz-top", "erz-cards"} if alias in {"erz-top", "erz-cards"} else {alias}
                neighbours = {key: self.inventory(candidate, key) for key in sa.SOURCES - paired}
                metadata = p.select_candidate_sources(candidate, run_status(failed=[alias]), previous)
                for key in paired:
                    self.assertEqual(metadata["sources"][key]["state"], "retained")
                    self.assertEqual(self.inventory(candidate, key), self.inventory(previous, key))
                for key, expected in neighbours.items():
                    self.assertEqual(self.inventory(candidate, key), expected)

    def test_cold_outage_builds_real_etl_all_marts_and_explicit_api_unavailability(self):
        # This is the real subprocess ETL/full9/check/API path, not a mocked
        # validator. Existing synthetic economic processed contributions are
        # current and reusable; Nashdom processed indicators are really rebuilt.
        from fastapi.testclient import TestClient
        from backend.app import create_app
        for alias in ("monitoring", "fedstat", "rasprod", "kvart", "construction", "rosstat", "erz-top", "erz-cards"):
            with self.subTest(alias=alias):
                runtime = self.base / ("runtime-" + alias)
                before = {key: self.inventory(self.root, key) for key in sa.SOURCES}
                with patch.object(p, "swap"):
                    p.publish(self.root, runtime, run_status=run_status([alias]), collector_returncode=0)
                release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
                metadata = sa.read_publication(release)
                self.assertEqual(metadata["sources"][alias]["state"], "unavailable")
                self.assertTrue(metadata["full_marts_built"])
                with patch.dict(os.environ, {"PARSER_ROOT": str(release), "PARSER_DOWNLOADS": str(release / "downloads"), "PARSER_REQUIRE_REALTY_MARTS": "1"}), TestClient(create_app()) as client:
                    self.assertEqual(client.get("/api/v1/health").status_code, 200)
                    self.assertEqual(client.get("/api/v1/availability").json()["sources"][alias]["state"], "unavailable")
                    route = next((route for route in ("/api/v1/catalog", "/api/v1/apartments", "/api/v1/sales", "/api/v1/construction", "/api/v1/economics/salary", "/api/v1/economics/accounts") if alias in sa.route_sources(route)), None)
                    if route:
                        response = client.get(route)
                        self.assertEqual(response.status_code, 503)
                        self.assertEqual(response.json()["detail"]["code"], "source_unavailable")
                self.assertEqual({key: self.inventory(self.root, key) for key in sa.SOURCES}, before)

    def test_all_eight_success_health_proof_is_cached_and_manifest_change_fails(self):
        from fastapi.testclient import TestClient
        from backend.app import create_app
        runtime = self.base / "runtime"
        with patch.object(p, "swap"):
            p.publish(self.root, runtime, run_status=run_status(), collector_returncode=0)
        release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        with patch.dict(os.environ, {"PARSER_ROOT": str(release), "PARSER_DOWNLOADS": str(release / "downloads"), "PARSER_REQUIRE_REALTY_MARTS": "1"}), \
                patch.object(sa, "verify_manifest", wraps=sa.verify_manifest) as proof, TestClient(create_app()) as client:
            for _ in range(2):
                response = client.get("/api/v1/health")
                self.assertEqual(response.status_code, 200)
                self.assertEqual(response.json()["dataMode"], "live")
            self.assertEqual(proof.call_count, 1)
            path = release / "data/marts/realty/manifest.json"
            manifest = json.loads(path.read_text())
            manifest["marts"]["kvartirografia"]["summary"] = {"type": "dict", "keys": []}
            path.write_text(json.dumps(manifest))
            self.assertEqual(client.get("/api/v1/health").status_code, 503)
            self.assertEqual(proof.call_count, 2)

    def test_retained_fedstat_cannot_roll_back_fresh_rosstat_and_invalid_current_source_is_retained(self):
        from pipeline.data_access import DataAccess, DataContext
        import pandas as pd
        runtime = self.base / "runtime"
        previous = runtime / "releases/old"
        shutil.copytree(self.seed, previous)
        old_fed = self.inventory(previous, "fedstat")
        file = next(file for file in sa.source_files(self.root, "fedstat") if file.suffix == ".xlsx")
        file.write_bytes(b"partial download")
        target = self.root / "data/processed/gdp_rf.pkl"
        frame = pd.read_pickle(target)
        frame["value"] = 222.5
        frame.to_pickle(target)
        current_ros = self.inventory(self.root, "rosstat")
        with patch.object(p, "live_target", return_value="releases/old"), patch.object(p, "swap"):
            p.publish(self.root, runtime, run_status=run_status(["fedstat"]), collector_returncode=0)
        release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        self.assertEqual(self.inventory(release, "fedstat"), old_fed)
        self.assertEqual(self.inventory(release, "rosstat"), current_ros)
        self.assertTrue(pd.read_pickle(release / "data/processed/gdp_rf.pkl").value.eq(222.5).all())
        self.assertTrue(pd.read_pickle(previous / "data/processed/gdp_rf.pkl").value.eq(100.5).all())
        candidate = self.base / "bad-current"
        shutil.copytree(self.seed, candidate)
        next(file for file in sa.source_files(candidate, "kvart") if file.suffix == ".json").write_text("not JSON")
        metadata = p.select_candidate_sources(candidate, run_status(), previous)
        self.assertEqual(metadata["sources"]["kvart"]["state"], "retained")
        self.assertEqual(metadata["sources"]["kvart"]["reason_code"], "invalid_collected_data")
        self.assertEqual(self.inventory(candidate, "kvart"), self.inventory(previous, "kvart"))

    def test_failed_collectors_retain_exact_previous_families_while_monitoring_and_salary_publish(self):
        from backend.app import create_app
        from fastapi.testclient import TestClient
        import pandas as pd
        runtime = self.base / "runtime"
        previous = runtime / "releases/old"
        shutil.copytree(self.seed, previous)
        failed = ["construction", "kvart", "rasprod", "rosstat"]
        old = {alias: self.inventory(previous, alias) for alias in sa.SOURCES}
        for alias in failed:
            files = sa.source_files(self.root, alias)
            next(file for file in files if file.suffix in {".json", ".xlsx"}).write_bytes(b"partial failed download")
            for key in sa.indicator_ids({alias}):
                (self.root / "data/processed" / (key + ".pkl")).unlink(missing_ok=True)
        monitor = next((self.root / "data/raw/realty/nashdom").glob("monitoring_*.xlsx"))
        monitor.rename(monitor.with_name("monitoring_2_0_20261105.xlsx"))
        salary = self.root / "data/processed/avg_salary.pkl"
        frame = pd.read_pickle(salary)
        frame["value"] = 98765.0
        frame.to_pickle(salary)
        current = {alias: self.inventory(self.root, alias) for alias in sa.SOURCES}
        status = run_status(failed=failed)
        original_status = copy.deepcopy(status)
        with patch.object(p, "live_target", return_value="releases/old"), patch.object(p, "swap"):
            p.publish(self.root, runtime, run_status=status, collector_returncode=2)
        release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        declaration = sa.read_publication(release)
        self.assertEqual(declaration["status"], "degraded")
        self.assertEqual(declaration["collector_status"], "failed")
        self.assertEqual(declaration["collector_returncode"], 2)
        for alias in failed:
            self.assertEqual(self.inventory(release, alias), old[alias])
            self.assertEqual(declaration["sources"][alias]["state"], "retained")
            self.assertEqual(declaration["sources"][alias]["outcome"]["status"], "failed")
        self.assertEqual(self.inventory(release, "monitoring"), current["monitoring"])
        self.assertTrue(pd.read_pickle(release / "data/processed/avg_salary.pkl").value.eq(98765).all())
        self.assertEqual(status, original_status)
        self.assertEqual({alias: self.inventory(self.root, alias) for alias in sa.SOURCES}, current)
        self.assertEqual({alias: self.inventory(previous, alias) for alias in sa.SOURCES}, old)
        with patch.dict(os.environ, {"PARSER_ROOT": str(release), "PARSER_DOWNLOADS": str(release / "downloads"),
                                     "PARSER_REQUIRE_REALTY_MARTS": "1"}), TestClient(create_app()) as client:
            self.assertEqual(client.get("/api/v1/health").status_code, 200)
            self.assertEqual(client.get("/api/v1/economics/salary/catalog").status_code, 200)
            response = client.get("/api/v1/construction/permits")
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.json()["source"]["date"], "2026-11-05")

    def test_cold_failed_source_has_no_partial_data_and_other_sources_still_publish(self):
        runtime = self.base / "runtime"
        status = run_status(failed=["kvart"])
        with patch.object(p, "swap"):
            p.publish(self.root, runtime, run_status=status, collector_returncode=2)
        release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        declaration = sa.read_publication(release)
        self.assertEqual(declaration["sources"]["kvart"]["state"], "unavailable")
        self.assertEqual(declaration["sources"]["monitoring"]["state"], "fresh")
        self.assertFalse(sa.source_files(release, "kvart"))
        self.assertFalse((release / "data/processed/realty_kvartirografia.pkl").exists())

    def test_bootstrap_baseline_is_captured_before_collector_and_does_not_restore_working_files(self):
        runtime = self.base / "runtime"
        for name in ("nashdom_checker.py", "erzrf_checker.py", "fedstat_checker.py"):
            (self.root / name).write_text("test")
        old = self.inventory(self.root, "construction")
        original = subprocess.run
        def run(command, **kwargs):
            if "scripts/update_realty.py" in command:
                self.assertIn("--collect-only", command)
                next(iter(sa.source_files(self.root, "construction"))).write_bytes(b"partial bad JSON")
                (self.root / "data/processed/realty_update_status.json").write_text(json.dumps(run_status(failed=["construction"])))
                return subprocess.CompletedProcess(command, 2)
            return original(command, **kwargs)
        with patch.object(p.subprocess, "run", side_effect=run), patch.object(p, "swap"):
            p.run_action("update", self.root, runtime)
        release = runtime / json.loads((runtime / "pending.json").read_text())["next"]
        self.assertEqual(self.inventory(release, "construction"), old)
        self.assertNotEqual(self.inventory(self.root, "construction"), old)
        self.assertFalse(list(runtime.glob(".bootstrap-baseline-*")))


if __name__ == "__main__":
    unittest.main()
