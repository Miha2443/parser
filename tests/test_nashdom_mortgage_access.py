"""Readiness waits, genuine access failures and private diagnostic contracts."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from selenium.common.exceptions import InvalidSessionIdException, JavascriptException, TimeoutException

from pipeline.downloaders.nashdom_mortgage import AccessVerificationRequired, BrowserClient, PageNotReady
from pipeline.downloaders.nashdom_mortgage_access import ACCESS_STATE_SCRIPT, read_access_state


def state(**changes):
    return {"sameOrigin": True, "expectedPath": True, "controlsReady": True,
            "readyState": "complete", "loaderVisible": False,
            "captchaVisible": False, "denied": False, "title": "Mortgage", **changes}


class Wait:
    def __init__(self, driver, timeout, **kwargs):
        self.driver = driver

    def until(self, callback, message=""):
        for _ in range(8):
            result = callback(self.driver)
            if result:
                return result
        raise TimeoutException(message)


class AccessStateTests(unittest.TestCase):
    def test_readiness_requires_document_controls_origin_path_and_no_visible_blocker(self):
        driver = Mock()
        driver.execute_script.return_value = state()
        self.assertTrue(read_access_state(driver)["ready"])
        for changes in ({"controlsReady": False}, {"sameOrigin": False}, {"expectedPath": False},
                        {"readyState": "loading"}, {"loaderVisible": True},
                        {"captchaVisible": True}, {"denied": True}):
            driver.execute_script.return_value = state(**changes)
            self.assertFalse(read_access_state(driver)["ready"], changes)
        driver.execute_script.return_value = state(readyState="interactive")
        self.assertTrue(read_access_state(driver)["ready"])

    def test_metadata_omits_html_headers_queries_and_unknown_fields(self):
        driver = Mock()
        driver.execute_script.return_value = state(html="PRIVATE", url="?token=PRIVATE", cookies="PRIVATE")
        result = read_access_state(driver)
        self.assertNotIn("PRIVATE", json.dumps(result))
        args = driver.execute_script.call_args.args
        self.assertEqual(args[0], ACCESS_STATE_SCRIPT)
        self.assertTrue(args[1].startswith("https://xn--"))

    def test_invalid_or_ambiguous_observations_do_not_pass(self):
        driver = Mock()
        for value in (None, {}, state(captchaVisible="false"), state(readyState="unknown")):
            driver.execute_script.return_value = value
            with self.assertRaises(RuntimeError):
                read_access_state(driver)


class BrowserStartupTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.raw = Path(self.tmp.name)
        self.driver, self.capture = Mock(), Mock()
        self.driver.get_screenshot_as_png.return_value = b"private screenshot"
        self.client = BrowserClient(self.raw, timeout=1)
        self.observations, self.events = [state()], []

        def execute(script, *args):
            if script != ACCESS_STATE_SCRIPT:
                self.events.append("tracker_installed")
                return None
            self.events.append("observe")
            current = self.observations.pop(0) if len(self.observations) > 1 else self.observations[0]
            if isinstance(current, Exception):
                raise current
            return copy.deepcopy(current)

        self.driver.execute_script.side_effect = execute
        self.capture.enable.side_effect = lambda: self.events.append("network_enabled")
        self.driver.get.side_effect = lambda url: self.events.append("navigate")
        self.enterContext(patch("pipeline.selenium_utils.create_chrome", return_value=self.driver))
        self.enterContext(patch("pipeline.downloaders.nashdom_mortgage.MortgageResponseCapture", return_value=self.capture))
        self.enterContext(patch("selenium.webdriver.support.ui.WebDriverWait", Wait))
        self.enterContext(patch("sys.stderr"))

    def diagnostics(self):
        return sorted((self.raw / ".mortgage_history/diagnostics").glob("*.json"))

    def test_loader_then_controls_waits_without_reopening_or_calling_it_captcha(self):
        self.observations = [state(loaderVisible=True, controlsReady=False),
                             state(readyState="loading", controlsReady=False), state()]
        with patch.object(self.client, "_wait") as verified:
            with self.client:
                self.assertEqual(self.capture.poll.call_count, 3)
                verified.assert_called_once()
        self.assertEqual(self.driver.get.call_count, 1)
        self.assertEqual(self.events[:2], ["network_enabled", "navigate"])
        self.assertEqual(self.events[-1], "tracker_installed")
        self.assertFalse(self.diagnostics())
        self.driver.quit.assert_called_once()

    def test_visible_captcha_and_denial_stop_once_with_private_evidence(self):
        for flags, message in (({"captchaVisible": True}, "visible CAPTCHA"),
                               ({"denied": True}, "access-denied")):
            with self.subTest(flags=flags):
                self.driver.reset_mock()
                self.observations = [state(**flags)]
                before = set(self.diagnostics())
                with self.assertRaisesRegex(AccessVerificationRequired, message):
                    self.client.__enter__()
                self.driver.get.assert_called_once()
                self.driver.quit.assert_called_once()
                path = next(iter(set(self.diagnostics()) - before))
                evidence = json.loads(path.read_text(encoding="utf-8"))
                self.assertEqual(evidence["stage"], "document")
                self.assertEqual(evidence["errorClass"], "AccessVerificationRequired")
                self.assertTrue(evidence["state"][next(iter(flags))])

    def test_persistent_loader_is_page_not_ready_not_manual_verification(self):
        self.observations = [state(loaderVisible=True, controlsReady=False)]
        with self.assertRaises(PageNotReady):
            self.client.__enter__()
        self.driver.get.assert_called_once()
        self.driver.quit.assert_called_once()
        evidence = json.loads(self.diagnostics()[0].read_text(encoding="utf-8"))
        self.assertEqual(evidence["errorClass"], "PageNotReady")
        self.assertFalse(evidence["state"]["captchaVisible"])

    def test_page_load_timeout_can_finish_with_real_controls_without_refresh(self):
        self.driver.get.side_effect = TimeoutException("Slow assets")
        with patch.object(self.client, "_wait"):
            with self.client:
                self.assertTrue(self.client._navigation_timed_out)
        self.driver.get.assert_called_once()
        self.driver.refresh.assert_not_called()
        self.driver.execute_script.assert_any_call(ACCESS_STATE_SCRIPT,
            "https://xn--80az8a.xn--d1aqf.xn--p1ai", "/аналитика/ипотечное_кредитование")
        self.assertFalse(self.diagnostics())

    def test_document_context_change_may_settle_but_dead_session_is_not_retried(self):
        self.observations = [JavascriptException("execution context was destroyed"), state()]
        with patch.object(self.client, "_wait"):
            with self.client:
                pass
        self.observations = [InvalidSessionIdException("Session gone")]
        self.driver.reset_mock()
        with self.assertRaises(InvalidSessionIdException):
            self.client.__enter__()
        self.driver.get.assert_called_once()
        self.driver.quit.assert_called_once()

    def test_diagnostics_are_bounded_and_do_not_publish_sensitive_text(self):
        self.client.driver = self.driver
        self.client._last_access_state = read_access_state(self.driver)
        self.client._last_access_state["title"] = "PRIVATE_TITLE"
        for _ in range(8):
            self.client._diagnostic("document", RuntimeError("PRIVATE_ERROR ?token=PRIVATE_TOKEN"))
        self.assertEqual(len(self.diagnostics()), 5)
        directory = self.diagnostics()[0].parent
        self.assertEqual(len(list(directory.glob("*.png"))), 5)
        for path in self.diagnostics():
            text = path.read_text(encoding="utf-8")
            self.assertNotIn("PRIVATE", text)
            self.assertTrue((directory / json.loads(text)["screenshot"]).is_file())
        from docker.publication import copy_tree
        with TemporaryDirectory() as output:
            target = Path(output) / "published"
            copy_tree(self.raw, target)
            self.assertFalse((target / ".mortgage_history").exists())

    def test_access_failure_cannot_replace_existing_history(self):
        from pipeline.downloaders.nashdom_mortgage import RAW_PATH, collect
        from tests.test_nashdom_mortgage import FakeClient
        collect(self.raw, client_factory=FakeClient())
        path = self.raw / RAW_PATH / "mortgage_history.json"
        before = path.read_bytes()
        self.observations = [state(captchaVisible=True)]
        with self.assertRaises(AccessVerificationRequired):
            collect(self.raw)
        self.assertEqual(path.read_bytes(), before)

    def test_diagnostic_failure_does_not_mask_genuine_access_error(self):
        self.observations = [state(captchaVisible=True)]
        with patch("pathlib.Path.mkdir", side_effect=OSError("Disk unavailable")):
            with self.assertRaisesRegex(AccessVerificationRequired, "visible CAPTCHA"):
                self.client.__enter__()
        self.driver.quit.assert_called_once()

    def test_failed_metadata_does_not_leak_orphan_screenshots(self):
        self.client.driver = self.driver
        original = Path.open

        def open_file(path, *args, **kwargs):
            if path.suffix == ".json":
                raise OSError("Metadata write failed")
            return original(path, *args, **kwargs)

        with patch("pathlib.Path.open", new=open_file):
            for _ in range(7):
                self.client._diagnostic("document", PageNotReady("Loading"))
        directory = self.raw / ".mortgage_history/diagnostics"
        self.assertFalse(list(directory.glob("page_*.png")))
        orphan = directory / "page_older_orphan.png"
        orphan.write_bytes(b"old private screenshot")
        self.client._diagnostic("document", PageNotReady("Loading"))
        self.assertFalse(orphan.exists())
        self.assertEqual(len(list(directory.glob("*.png"))), 1)
        self.assertEqual(len(self.diagnostics()), 1)

    def test_partial_metadata_write_removes_both_incomplete_files(self):
        self.client.driver = self.driver

        def partial_dump(value, stream, **kwargs):
            stream.write('{"partial":')
            raise OSError("Disk full after metadata creation")

        with patch("pipeline.downloaders.nashdom_mortgage.json.dump", side_effect=partial_dump):
            for _ in range(7):
                self.client._diagnostic("document", PageNotReady("Loading"))
        directory = self.raw / ".mortgage_history/diagnostics"
        self.assertFalse(list(directory.glob("page_*")))


if __name__ == "__main__":
    unittest.main()
