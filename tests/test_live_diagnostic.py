"""Diagnostics disclose only bounded, allowlisted metadata and never retry GETs."""
import json
import os
import time
import unittest
from unittest.mock import Mock, patch

from docker.tests import live_diagnostic as probe


class LiveDiagnosticTests(unittest.TestCase):
    def test_urls_and_errors_remove_secrets_and_bound_visible_text(self):
        self.assertEqual(probe.safe_url("https://user:password@example.test/path?token=secret#cookie"), "https://example.test/path")
        value = probe.safe_text("token='supersecret' Cookie=private https://x.test/page?password=bad Bearer abc " + "x" * 500)
        self.assertLessEqual(len(value), 300)
        self.assertNotIn("supersecret", value)
        self.assertNotIn("private", value)
        self.assertNotIn("password=bad", value)
        self.assertNotIn("Bearer abc", value)
        self.assertNotIn("private", probe.safe_text('{"Cookie":"private"}'))

    def test_network_records_cannot_export_headers_cookies_or_full_events(self):
        def event(method, params):
            return {"message": json.dumps({"message": {"method": method, "params": params}})}
        entries = [event("Network.requestWillBeSent", {"requestId": "1", "request": {
                    "url": "https://example.test/page?token=private", "headers": {"Cookie": "SECRET"}, "postData": "PRIVATE"}}),
                   event("Network.responseReceived", {"requestId": "1", "type": "Document", "response": {
                    "url": "https://example.test/page?token=private", "status": 200, "headers": {"Set-Cookie": "SECRET"},
                    "securityDetails": {"issuer": "Public CA", "certificate": "PRIVATE"}}}),
                   event("Network.loadingFailed", {"requestId": "1", "type": "Document", "errorText": "net::ERR_CERT_AUTHORITY_INVALID"})]
        record = probe.network_metadata(entries)
        self.assertEqual(record["document_responses"], [{"url": "https://example.test/page", "status": 200}])
        self.assertEqual(record["certificate_issuers"], ["Public CA"])
        self.assertIn("ERR_CERT", record["loading_failed"][0]["error"])
        text = json.dumps(record)
        self.assertNotIn("SECRET", text)
        self.assertNotIn("PRIVATE", text)
        self.assertNotIn("private", text)

    def test_browser_uses_the_source_factory_and_one_get_even_after_navigation_error(self):
        driver = Mock()
        driver.capabilities = {"browserVersion": "155", "chrome": {"chromedriverVersion": "155"}}
        driver.get.side_effect = RuntimeError("token=secret")
        driver.get_log.return_value = []
        driver.execute_script.return_value = {"title": "Public source", "current_url": "https://example.test/page?token=secret",
                                              "ready_state": "complete", "body_text": "Ready", "dom_counts": {"body": 1}}
        from selenium import webdriver
        from selenium.webdriver.chrome.options import Options
        factory = Mock(side_effect=lambda **kwargs: webdriver.Chrome(options=Options()))
        with patch.object(webdriver, "Chrome", return_value=driver) as actual, \
                patch.object(probe, "page_ready", return_value=True):
            result = probe.browser_probe("kvart", "https://example.test/page", factory, deadline=time.monotonic() + 15)
        options = actual.call_args.kwargs["options"]
        self.assertEqual(options.capabilities["goog:loggingPrefs"], {"performance": "ALL", "browser": "ALL"})
        self.assertFalse(options.capabilities.get("acceptInsecureCerts", False))
        driver.get.assert_called_once_with("https://example.test/page")
        factory.assert_called_once()
        self.assertTrue(result["ready"])
        self.assertEqual(result["navigation_calls"], 1)
        self.assertNotIn("secret", json.dumps(result))
        driver.quit.assert_called_once()

    def test_requests_use_one_verified_get_without_redirect_following(self):
        import requests
        response = Mock(status_code=200, url="https://example.test/page?token=secret")
        response.raw.connection.sock = None
        response.__enter__ = Mock(return_value=response)
        response.__exit__ = Mock(return_value=False)
        with patch.object(requests, "get", return_value=response) as request:
            result = probe.requests_probe("https://example.test/page")
        request.assert_called_once()
        self.assertTrue(request.call_args.kwargs["verify"])
        self.assertFalse(request.call_args.kwargs["allow_redirects"])
        self.assertEqual(result["current_url"], "https://example.test/page")

    def test_broken_dom_capture_cannot_claim_ready_after_a_successful_predicate(self):
        driver = Mock()
        driver.capabilities = {}
        driver.execute_script.side_effect = RuntimeError("DOM unavailable")
        driver.get_log.return_value = []
        with patch.object(probe, "page_ready", return_value=True):
            result = probe.browser_probe("kvart", "https://example.test/page", Mock(return_value=driver), deadline=time.monotonic() + 15)
        self.assertFalse(result["ready"])
        self.assertIn("probe_error", result)
        driver.get.assert_called_once()

    def test_probe_does_not_run_unless_diagnostic_mode_explicitly_selected(self):
        with patch.dict(os.environ, {"PARSER_DIAGNOSTIC_ONLY": "0"}), self.assertRaises(RuntimeError):
            probe.main()


if __name__ == "__main__":
    unittest.main()
