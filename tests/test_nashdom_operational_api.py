"""Offline API-first transport, identity, evidence, and cleanup regressions."""
from contextlib import redirect_stdout
from io import StringIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
from selenium.common.exceptions import TimeoutException, WebDriverException

from pipeline.downloaders import nashdom_operational_api as api
from scripts.check_nashdom_operational_history import fixture


def replies():
    result = {
        api.PATHS["construction"] + "/actualDate": {
            "errcode": "0", "data": {"complete": True, "year": 2026, "month": 10}},
        api.PATHS["sales"] + "/actualDate": {
            "errcode": "0", "data": {"complete": True, "year": 2026, "month": 9}},
    }
    for block in ("construction", "sales"):
        for response in fixture(block, "2020-01")["responses"].values():
            result[response["url"]] = response["payload"]
            if "region_identity" in response:
                result[response["url"].replace("/total?", "/region?")] = response["region_identity"]
    return result


class HttpResponse:
    def __init__(self, payload=None, *, status=200, body=None, chunks=None):
        self.status_code = status
        self.headers = {"Content-Type": "application/json" if body is None else "text/html"}
        self.body = json.dumps(payload).encode() if body is None else body
        self.chunks = chunks
        self.closed = False
        self.read_sizes = []

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.closed = True

    def iter_content(self, chunk_size):
        self.read_sizes.append(chunk_size)
        if self.chunks is not None:
            yield from self.chunks
        else:
            for start in range(0, len(self.body), chunk_size):
                yield self.body[start:start + chunk_size]


class HttpSession:
    def __init__(self, payloads=None, *, error=None):
        self.payloads = payloads or replies()
        self.error = error
        self.headers = {}
        self.calls = []
        self.responses = []
        self.closed = False

    def get(self, url, **kwargs):
        self.calls.append((url, kwargs))
        if self.error is not None:
            if isinstance(self.error, BaseException):
                raise self.error
            response = self.error()
        else:
            response = HttpResponse(self.payloads[url.removeprefix(api.BASE)])
        self.responses.append(response)
        return response

    def close(self):
        self.closed = True


class Browser:
    """No webpack and no dashboard controls, even after navigation."""
    def __init__(self, payloads=None, *, origin=True, fetch_error=None):
        self.payloads = payloads or replies()
        self.origin = origin
        self.fetch_error = fetch_error
        self.get = Mock()
        self.quit = Mock()
        self.set_page_load_timeout = Mock()
        self.set_script_timeout = Mock()
        self.execute_cdp_cmd = Mock(side_effect=self.cdp)
        self.scripts = []
        self.fetches = []

    def cdp(self, command, params):
        if command == "Browser.getVersion":
            return {"product": "HeadlessChrome/154.0.1234.5"}
        return {}

    def execute_script(self, script, *args):
        self.scripts.append(script)
        if "location.origin ===" in script:
            return self.origin
        if "readyState:document.readyState" in script:
            return {"url": api.BASE + "/", "title": "Loading", "readyState": "interactive",
                    "body": "", "html": "<html><body></body></html>"}
        if script == "window.stop();":
            return None
        raise AssertionError("unexpected dashboard dependency: " + script)

    def execute_async_script(self, script, url, limit):
        self.fetches.append(url)
        if self.fetch_error is not None:
            if isinstance(self.fetch_error, BaseException):
                raise self.fetch_error
            return self.fetch_error
        assert "AbortController" in script and "redirect:'error'" in script
        assert "reader.read()" in script and limit == api.MAX_RESPONSE_BYTES
        return {"status": 200, "body": json.dumps(self.payloads[url]), "content_type": "application/json"}


class ImmediateWait:
    def __init__(self, driver, timeout):
        self.driver = driver

    def until(self, predicate):
        if not predicate(self.driver):
            raise TimeoutException("")
        return True


class OperationalTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.directory)
        self.enterContext(patch.object(api.time, "sleep"))
        self.enterContext(patch.object(api, "WebDriverWait", ImmediateWait))
        self.output = self.enterContext(redirect_stdout(StringIO()))

    def client(self, session, browser=None):
        self.enterContext(patch.object(api.requests, "Session", return_value=session))
        factory = self.enterContext(patch("pipeline.selenium_utils.create_chrome", return_value=browser))
        return api.OfficialClient(self.root, delay=0), factory

    def diagnostics(self):
        return sorted((self.root / ".construction_history" / "diagnostics").glob("transport_*.json"))

    def test_direct_api_reads_independent_bounds_and_months_without_browser(self):
        session = HttpSession()
        client, factory = self.client(session)
        with client as source:
            self.assertEqual(source.bounds, {"sales": "2026-09", "construction": "2026-10"})
            for block in ("construction", "sales"):
                item = source.collect(block, "2020-01")
                self.assertTrue(all(row["report_date"] == "" for row in item["rows"]))
                self.assertTrue(all(row["period_verified"] for row in item["rows"]))
        factory.assert_not_called()
        self.assertTrue(session.closed)
        self.assertTrue(all(response.closed for response in session.responses))
        self.assertEqual(len(session.calls), 7)
        for url, options in session.calls:
            self.assertTrue(url.startswith(api.BASE + api.API))
            self.assertEqual(options, {"timeout": (5, 20), "stream": True, "allow_redirects": False})
        self.assertFalse(self.diagnostics())

    def test_incomplete_and_malformed_bounds_are_not_guessed_or_fallen_back(self):
        for change in ({"complete": False}, {"year": "2026"}, {"month": True}, {"month": 13}, {"month": None}):
            with self.subTest(change=change):
                payloads = replies()
                payloads[api.PATHS["sales"] + "/actualDate"]["data"].update(change)
                session = HttpSession(payloads)
                client, factory = self.client(session)
                with self.assertRaises(api.ReportError):
                    client.__enter__()
                factory.assert_not_called()
                self.assertTrue(session.closed)
                self.assertEqual(client.bounds, {})

    def test_single_linux_browser_works_without_webpack_or_dynamic_dashboard(self):
        session = HttpSession(error=requests.ConnectionError("connection refused"))
        browser = Browser()
        client, factory = self.client(session, browser)
        with patch.object(api.sys, "platform", "linux"), client as source:
            self.assertEqual(source.bounds["construction"], "2026-10")
            source.collect("sales", "2020-01")
            source.collect("construction", "2020-01")
        factory.assert_called_once_with(download_dir=self.root, headless=True, page_load_strategy="none")
        browser.get.assert_called_once_with(api.BASE + api.PATHS["sales"] + "/actualDate")
        self.assertEqual(len(session.calls), 2)
        self.assertEqual(len(browser.fetches), 7)
        overrides = [call.args[1]["userAgent"] for call in browser.execute_cdp_cmd.call_args_list
                     if call.args[0] == "Network.setUserAgentOverride"]
        self.assertIn("X11; Linux x86_64", overrides[0])
        self.assertIn("Chrome/154.0.1234.5", overrides[0])
        self.assertNotIn("124", overrides[0])
        self.assertFalse(any("webpack" in script or "innerText.includes" in script for script in browser.scripts))
        browser.quit.assert_called_once()
        self.assertTrue(session.closed)

    def test_blank_origin_wait_has_typed_diagnostic_and_closes_resources(self):
        session = HttpSession(error=lambda: HttpResponse(status=403, body=b"Forbidden"))
        browser = Browser(origin=False)
        client, _ = self.client(session, browser)
        with self.assertRaisesRegex(api.TransportError, "TimeoutException: <empty message>.*diagnostics="):
            client.__enter__()
        browser.quit.assert_called_once()
        self.assertIsNone(client.driver)
        self.assertTrue(session.closed)
        evidence = json.loads(self.diagnostics()[-1].read_text())
        self.assertEqual(evidence["stage"], "browser_initialization")
        self.assertEqual(evidence["browser"]["title"], "Loading")
        self.assertEqual(evidence["browser"]["body"], "")
        self.assertTrue(any(event.get("status") == 403 for event in evidence["events"]))
        self.assertNotIn("WAF", json.dumps(evidence))

    def test_navigation_timeout_at_official_origin_does_not_require_hydration(self):
        session = HttpSession(error=requests.Timeout("connect timed out"))
        browser = Browser()
        browser.get.side_effect = TimeoutException("")
        client, _ = self.client(session, browser)
        with client:
            self.assertEqual(client.bounds["sales"], "2026-09")
        self.assertIn("window.stop();", browser.scripts)
        browser.quit.assert_called_once()

    def test_cdp_initialization_error_and_failed_quit_preserve_original_error(self):
        session = HttpSession(error=requests.ConnectionError("offline"))
        browser = Browser()
        browser.execute_cdp_cmd.side_effect = WebDriverException("CDP failed")
        browser.quit.side_effect = WebDriverException("quit failed")
        client, _ = self.client(session, browser)
        with self.assertRaisesRegex(api.TransportError, "CDP failed"):
            client.__enter__()
        browser.quit.assert_called_once()
        self.assertTrue(session.closed)
        client.__exit__()
        browser.quit.assert_called_once()

    def test_blank_async_timeout_is_retried_twice_and_saved_not_source_empty(self):
        session = HttpSession(error=requests.Timeout("read timed out"))
        browser = Browser(fetch_error=TimeoutException(""))
        client, factory = self.client(session, browser)
        with self.assertRaisesRegex(api.TransportError, "TimeoutException: <empty message>"):
            client.__enter__()
        self.assertEqual(len(browser.fetches), 2)
        factory.assert_called_once()
        browser.quit.assert_called_once()
        self.assertTrue(session.closed)
        evidence = json.loads(self.diagnostics()[-1].read_text())
        self.assertEqual(evidence["stage"], "browser_fetch")
        self.assertTrue(any("read timed out" in event.get("error", "") for event in evidence["events"]))

    def test_non_json_http_switches_transport_but_api_error_does_not(self):
        for response in (lambda: HttpResponse(body=b"<html>unavailable</html>"),
                         lambda: HttpResponse({"errcode": "42", "data": []})):
            session, browser = HttpSession(error=response), Browser()
            client, factory = self.client(session, browser)
            if response().body.startswith(b"<"):
                with client:
                    self.assertTrue(client.bounds)
                factory.assert_called_once()
            else:
                with self.assertRaisesRegex(api.ReportError, "API error envelope"):
                    client.__enter__()
                factory.assert_not_called()
            self.assertTrue(session.closed)

    def test_oversized_and_slow_direct_reads_are_bounded_and_closed(self):
        for response in (lambda: HttpResponse(chunks=[b"x" * 10, b"x"]),
                         lambda: HttpResponse({"errcode": "0"})):
            session = HttpSession(error=response)
            client = api.OfficialClient(self.root, delay=0)
            client.session = session
            if response().chunks:
                context = patch.object(api, "MAX_RESPONSE_BYTES", 10)
                expected = "byte limit"
            else:
                context = patch.object(api.time, "monotonic", side_effect=[0, 26, 30, 56])
                expected = "time limit"
            with context, self.assertRaisesRegex(api.TransportError, expected):
                client._http_get(api.PATHS["sales"] + "/actualDate")
            self.assertEqual(len(session.calls), 2)
            self.assertTrue(all(response.closed for response in session.responses))

    def test_error_http_preview_and_diagnostic_retention_are_bounded(self):
        session = HttpSession(error=lambda: HttpResponse(status=503, body=b"x" * 100000))
        client = api.OfficialClient(self.root, delay=0)
        client.session = session
        url = api.PATHS["sales"] + "/actualDate"
        with self.assertRaises(api.TransportError):
            client._http_get(url)
        for number in range(8):
            client._diagnostic("test", url, requests.ConnectionError(str(number)))
        paths = self.diagnostics()
        self.assertEqual(len(paths), 5)
        self.assertTrue(all(path.stat().st_size < 20000 for path in paths))
        self.assertTrue(all(response.read_sizes == [2048] for response in session.responses))
        self.assertTrue(all(len(event.get("preview", "")) <= 2048 for event in client.events))
        self.assertFalse(list(self.root.glob("*.json")))

    def test_diagnostic_failure_does_not_mask_original_browser_error(self):
        session = HttpSession(error=requests.ConnectionError("offline"))
        browser = Browser(origin=False)
        client, _ = self.client(session, browser)
        with patch.object(Path, "write_text", side_effect=OSError("disk full")):
            with self.assertRaisesRegex(api.TransportError, "TimeoutException.*diagnostics unavailable.*disk full"):
                client.__enter__()
        browser.quit.assert_called_once()
        self.assertTrue(session.closed)

    def test_browser_snapshot_failure_does_not_mask_fetch_failure(self):
        session = HttpSession(error=requests.ConnectionError("offline"))
        browser = Browser(fetch_error={"error": "TypeError: Failed to fetch"})
        execute = browser.execute_script

        def failed_snapshot(script, *args):
            if "readyState:document.readyState" in script:
                raise WebDriverException("snapshot unavailable")
            return execute(script, *args)

        browser.execute_script = Mock(side_effect=failed_snapshot)
        client, _ = self.client(session, browser)
        with self.assertRaisesRegex(api.TransportError, "Failed to fetch"):
            client.__enter__()
        evidence = json.loads(self.diagnostics()[-1].read_text())
        self.assertIn("snapshot unavailable", evidence["browser_error"])
        browser.quit.assert_called_once()

    def test_browser_creation_failure_closes_http_session(self):
        session = HttpSession(error=requests.ConnectionError("offline"))
        client, factory = self.client(session)
        factory.side_effect = WebDriverException("Chrome could not start")
        with self.assertRaisesRegex(api.TransportError, "Chrome could not start.*diagnostics="):
            client.__enter__()
        factory.assert_called_once()
        self.assertTrue(session.closed)
        self.assertIsNone(client.driver)

    def test_collection_validation_error_closes_browser_and_http_session(self):
        payloads = replies()
        payloads[api.request_url("construction", "2020-01", "rf")]["data"][0]["repMonth"] = 10
        session = HttpSession(error=requests.ConnectionError("offline"))
        browser = Browser(payloads)
        client, factory = self.client(session, browser)
        with self.assertRaisesRegex(api.ReportError, "different period"), client:
            client.collect("construction", "2020-01")
        factory.assert_called_once()
        browser.quit.assert_called_once()
        self.assertTrue(session.closed)

    def test_empty_month_is_source_empty_without_latest_or_browser_fallback(self):
        payloads = replies()
        url = api.request_url("construction", "2020-01", "rf")
        payloads[url]["data"] = []
        session = HttpSession(payloads)
        client, factory = self.client(session)
        with client, self.assertRaises(api.EmptyReport):
            client.collect("construction", "2020-01")
        factory.assert_not_called()
        self.assertEqual([call[0] for call in session.calls[2:]],
                         [api.BASE + api.request_url("construction", "2020-01", region)
                          for region in ("rf", "msk")])

    def test_malformed_browser_responses_are_bounded_transport_errors(self):
        for response in (False, {"status": 200, "body": None}, {"status": 200, "body": "<html>busy</html>"},
                         {"status": 503, "body": "Unavailable"}):
            with self.subTest(response=response):
                session = HttpSession(error=requests.ConnectionError("offline"))
                browser = Browser(fetch_error=response)
                client, factory = self.client(session, browser)
                with self.assertRaises(api.TransportError):
                    client.__enter__()
                self.assertEqual(len(browser.fetches), 2)
                factory.assert_called_once()
                browser.quit.assert_called_once()
                self.assertTrue(session.closed)

    def test_direct_redirect_is_not_followed_and_browser_fetch_keeps_exact_scope(self):
        session = HttpSession(error=lambda: HttpResponse(status=302, body=b"redirect to latest"))
        browser = Browser()
        client, _ = self.client(session, browser)
        with client:
            client.collect("sales", "2020-01")
        self.assertTrue(all(options["allow_redirects"] is False for _, options in session.calls))
        self.assertTrue(all("repYear=2020&repMonth=1" in url for url in browser.fetches[2:]))

    def test_period_region_and_sales_identity_checks_remain_strict(self):
        for block, change in (("construction", "period"), ("construction", "region"), ("sales", "identity")):
            with self.subTest(block=block, change=change):
                payloads = replies()
                url = api.request_url(block, "2020-01", "msk")
                if change == "period":
                    payloads[url]["data"][0]["repMonth"] = 10
                elif change == "region":
                    payloads[url]["data"][0]["subject"] = "100"
                else:
                    payloads[url.replace("/total?", "/region?")]["data"][0]["totalSquare"] += 10
                session = HttpSession(payloads)
                client, factory = self.client(session)
                with client, self.assertRaises(api.ReportError):
                    client.collect(block, "2020-01")
                factory.assert_not_called()

    def test_future_month_and_non_official_paths_fail_without_network(self):
        session = HttpSession()
        client, factory = self.client(session)
        with client:
            count = len(session.calls)
            with self.assertRaisesRegex(api.ReportError, "exceeds official"):
                client.collect("sales", "2026-10")
            for url in ("https://example.org/total", "//example.org/total", api.API + "/unknown"):
                with self.assertRaisesRegex(api.ReportError, "only official"):
                    client.get(url)
            self.assertEqual(len(session.calls), count)
        factory.assert_not_called()


if __name__ == "__main__":
    unittest.main()
