"""Regression for 8.txt: HTTP failure must not bypass a usable browser collector.

Run the actual CLI lifecycle, locks, status writes and child processes in a
private directory. Only provider transports and the external collector payload
are fixtures; run_source / _collect_source / main remain production code.
"""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

from pipeline import source_availability as sa
from scripts import update_realty as ur


def evidence(url, status=None, **extra):
    return {"url": url, "checked_at": sa.stamp(), "http_status": status, "text": "", **extra}


class BrowserAvailabilityTests(unittest.TestCase):
    def test_http_failure_does_not_skip_any_healthy_browser_source(self):
        for alias in sorted(sa.BROWSER_SOURCES):
            with self.subTest(alias=alias), patch.object(sa, "http_check", side_effect=lambda url: evidence(url, network_error="connection_unavailable")), \
                    patch.object(sa, "browser_check", side_effect=lambda url: evidence(url, 200, transport="browser")) as browser:
                self.assertIsNone(sa.check_provider(alias, "test"))
                self.assertEqual(browser.call_count, len(sa.URLS[alias]))

    def test_two_browser_checks_required_and_legacy_evidence_still_validates(self):
        for answers, expected in (([503, 200], False), ([503, 503], True)):
            with self.subTest(answers=answers), patch.object(sa, "http_check", side_effect=lambda url: evidence(url, 503)), \
                    patch.object(sa, "browser_check", side_effect=[evidence(sa.URLS["erz-top"][0], value, transport="browser") for value in answers]):
                started = sa.stamp()
                row = sa.check_provider("erz-top", "test")
                self.assertEqual(row is not None, expected)
                if row:
                    self.assertEqual({item["transport"] for item in row["evidence"]}, {"browser"})
                    sa.validate_outcome(row, "erz-top", "test", started, sa.stamp())
        started = sa.stamp()
        row = sa.check_provider("erz-top", "old", check=lambda url: evidence(url, 503))
        sa.validate_outcome(row, "erz-top", "old", started, sa.stamp())

    def test_driver_navigation_and_tls_errors_are_not_provider_outages(self):
        from selenium.common.exceptions import TimeoutException, WebDriverException
        from pipeline import selenium_utils
        for exception, expected in ((TimeoutException("navigation timeout"), None),
                                    (WebDriverException("net::ERR_CERT_AUTHORITY_INVALID"), None),
                                    (WebDriverException("net::ERR_CONNECTION_CLOSED"), "provider_unreachable")):
            driver = Mock()
            driver.get.side_effect = exception
            driver.execute_script.return_value = {"href": "chrome-error://chromewebdata/", "status": None, "text": "", "headings": []}
            with self.subTest(exception=type(exception).__name__, expected=expected), patch.object(selenium_utils, "create_chrome", return_value=driver):
                self.assertEqual(sa.classify_check(sa.browser_check(sa.URLS["erz-top"][0])), expected)
                driver.quit.assert_called_once()
        with patch.object(selenium_utils, "create_chrome", side_effect=WebDriverException("driver crashed")):
            self.assertIsNone(sa.classify_check(sa.browser_check(sa.URLS["erz-top"][0])))


class CollectionEntryTests(unittest.TestCase):
    def run_cli(self, *, skipped=(), guarded=True, sources=("erz-top", "erz-cards"), locked=False):
        with tempfile.TemporaryDirectory() as temporary, ExitStack() as stack:
            root = Path(temporary)
            marker = root / "collector-started.txt"
            script = root / "fixture_collector.py"
            script.write_text("from pathlib import Path\nimport sys\nwith Path(" + repr(str(marker)) + ").open('a') as file: file.write(sys.argv[1]+'\\n')\n", encoding="utf-8")
            for name, value in {"ROOT": root, "REALTY_ROOT": root / "data/raw/realty", "LOG_DIR": root / "logs",
                                "PROCESSED_DIR": root / "data/processed", "REALTY_STATUS_FILE": root / "data/processed/realty_update_status.json",
                                "REALTY_MARTS_MANIFEST": root / "data/marts/realty/manifest.json", "REALTY_UPDATE_LOCK": root / "state/.realty_update.lock",
                                "SNAPSHOT_DIRS": [(root / "data/raw/realty", "realty", "")]}.items():
                stack.enter_context(patch.object(ur, name, value))
            stack.enter_context(patch.dict(ur.SOURCE_MAP, {alias: (str(script), [alias]) for alias in sources}))
            stack.enter_context(patch.object(sa, "http_check", side_effect=lambda url: evidence(url, network_error="connection_unavailable")))
            blocked = {sa.URLS[alias][0] for alias in skipped}
            stack.enter_context(patch.object(sa, "browser_check", side_effect=lambda url: evidence(url, 503 if url in blocked else 200, transport="browser")))
            # Monitoring has no browser transport; its HTTP success is genuine.
            if "monitoring" in sources:
                stack.enter_context(patch.object(sa, "http_check", side_effect=lambda url: evidence(url, 200) if url in sa.URLS["monitoring"] else evidence(url, network_error="connection_unavailable")))
            arguments = ["update_realty.py", *sources, "--collect-only", "--no-archive", "--no-notify"]
            if guarded:
                arguments.append("--require-collected")
            stack.enter_context(patch.object(sys, "argv", arguments))
            if locked:
                stack.enter_context(patch.object(ur, "acquire_realty_update_lock", return_value=False))
            with redirect_stdout(StringIO()):
                code = ur.main()
            # The caller's && publication is only reachable with a zero exit.
            publish = root / "published"
            if code == 0:
                publish.touch()
            started = marker.read_text().splitlines() if marker.is_file() else []
            status_file = root / "data/processed/realty_update_status.json"
            status = json.loads(status_file.read_text(encoding="utf-8")) if status_file.is_file() else {}
            return code, started, status, publish.exists()

    def test_real_cli_runs_both_child_collectors_despite_http_connection_failure(self):
        code, started, status, published = self.run_cli()
        self.assertEqual(code, 0)
        self.assertEqual(started, ["erz-top", "erz-cards"])
        self.assertEqual(status["status"], "success")
        self.assertTrue(published)

    def test_manual_skipped_run_or_busy_lock_cannot_reach_chained_publication(self):
        code, started, status, published = self.run_cli(skipped=("erz-top", "erz-cards"))
        self.assertEqual((code, started, status["status"], published), (3, [], "degraded", False))
        self.assertTrue(status["require_collected"])
        code, started, status, published = self.run_cli(locked=True)
        self.assertEqual((code, started, published), (3, [], False))

    def test_manual_partial_skip_also_blocks_publication(self):
        code, started, status, published = self.run_cli(skipped=("erz-top",), sources=("monitoring", "erz-top", "erz-cards"))
        self.assertEqual((code, started, status["status"], published), (3, ["monitoring"], "degraded", False))

    def test_daily_degraded_run_still_collects_other_sources(self):
        code, started, status, published = self.run_cli(skipped=("erz-top",), guarded=False, sources=("monitoring", "erz-top", "erz-cards"))
        self.assertEqual((code, started, status["status"], published), (0, ["monitoring"], "degraded", True))
        self.assertEqual(set(status["skipped"]), {"erz-top", "erz-cards"})


if __name__ == "__main__":
    unittest.main()
