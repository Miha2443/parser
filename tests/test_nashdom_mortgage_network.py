"""Offline Chrome-log capture; no test opens a browser or sends requests."""
import base64
import copy
import json
import unittest
from unittest.mock import Mock, patch

from pipeline.downloaders.nashdom_mortgage_network import (
    MortgageResponseCapture, ResponseCaptureError,
)
from tests.test_nashdom_mortgage_api import exchanges


def event(method, **params):
    return {"message": json.dumps({"message": {"method": "Network." + method,
                                               "params": params}})}


def request(item, request_id, *, wall_time=100):
    return event("requestWillBeSent", requestId=request_id, wallTime=wall_time, request={
        "url": item["url"], "method": item["method"],
        "postData": json.dumps(item["request"]),
        "headers": {"Cookie": "private-cookie", "Authorization": "private-authorization"},
    })


def response(item, request_id, *, status=200, mime="application/json", url=None):
    return event("responseReceived", requestId=request_id, response={
        "url": url or item["url"], "status": status, "mimeType": mime,
        "headers": {"Set-Cookie": "private-cookie"},
    })


class Driver:
    def __init__(self):
        self.logs, self.bodies, self.calls = [], {}, []

    def get_log(self, name):
        if name != "performance":
            raise AssertionError(name)
        result, self.logs = self.logs, []
        return result

    def execute_cdp_cmd(self, name, params):
        self.calls.append((name, params))
        if name == "Network.enable":
            return {}
        if name != "Network.getResponseBody":
            raise AssertionError("Only reading already-received response bodies is allowed")
        value = self.bodies[params["requestId"]]
        if isinstance(value, list):
            value = value.pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    def add(self, region="rf", period="2025-08", *, prefix="", finished=True, wall_time=100):
        items = exchanges(region, period)
        for index, item in enumerate(items):
            request_id = prefix + str(index)
            self.logs.extend([request(item, request_id, wall_time=wall_time), response(item, request_id)])
            if finished:
                self.logs.append(event("loadingFinished", requestId=request_id))
            self.bodies[request_id] = {"body": json.dumps(item["body"]), "base64Encoded": False}
        return items


class CaptureTests(unittest.TestCase):
    def setUp(self):
        self.driver = Driver()
        self.capture = MortgageResponseCapture(self.driver)

    def test_exact_pair_preserves_values_and_excludes_private_headers(self):
        self.driver.add()
        self.capture.poll()
        result = self.capture.month("rf", "2025-08")
        self.assertEqual(result["metrics"]["all"]["volumeRub"], 392274000000)
        self.assertEqual(result["metrics"]["all"]["termMonths"], 307.000008)
        self.assertEqual(result["sourcePrecision"], "raw response")
        self.assertEqual(len(result["rawMetrics"]["exchanges"]), 2)
        evidence = json.dumps(result)
        for private in ("private-cookie", "private-authorization", "headers", "Authorization"):
            self.assertNotIn(private, evidence)
        self.assertEqual({name for name, _ in self.driver.calls}, {"Network.getResponseBody"})

    def test_new_ui_interaction_cannot_reuse_a_cached_pair(self):
        self.driver.add()
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08", generation=0))
        generation = self.capture.begin_interaction(not_before=101)
        self.assertEqual(generation, 1)
        self.assertIsNone(self.capture.month("rf", "2025-08", generation=generation))
        self.driver.add(prefix="next-", wall_time=102)
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08", generation=generation))
        self.assertIsNone(self.capture.month("rf", "2025-08", generation=0))

    def test_unknown_new_filter_invalidates_cached_pair_even_without_mark(self):
        self.driver.add()
        self.capture.poll()
        item = exchanges()[0]
        item["request"]["unknownFilter"] = "unsupported"
        self.driver.logs = [request(item, "unexpected")]
        self.capture.poll()
        with self.assertRaisesRegex(ResponseCaptureError, "Unexpected mortgage request"):
            self.capture.month("rf", "2025-08")
        self.assertFalse(self.capture._scopes)
        self.assertFalse(self.capture._requests)

    def test_unexpected_mortgage_endpoint_or_method_cannot_leave_a_cached_success(self):
        for modification in ({"url": exchanges()[0]["url"] + "?new=1"},
                             {"url": exchanges()[0]["url"] + "/"}, {"method": "GET"}):
            with self.subTest(modification=modification):
                driver = Driver()
                driver.add()
                capture = MortgageResponseCapture(driver)
                capture.poll()
                item = {**exchanges()[0], **modification}
                driver.logs = [request(item, "unexpected")]
                capture.poll()
                with self.assertRaises(ResponseCaptureError):
                    capture.month("rf", "2025-08")

    def test_unknown_filter_poison_requires_new_action_before_recovery(self):
        self.capture.begin_interaction(not_before=99)
        item = exchanges()[0]
        item["request"]["unknownFilter"] = 1
        self.driver.logs = [request(item, "unexpected")]
        self.driver.add(prefix="correct-")
        self.capture.poll()
        with self.assertRaises(ResponseCaptureError):
            self.capture.month("rf", "2025-08")
        self.capture.begin_interaction(not_before=101)
        self.driver.add(prefix="recovered-", wall_time=102)
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08", generation=2))

    def test_pair_must_be_complete_within_one_interaction_generation(self):
        self.driver.add()
        self.capture.poll()
        self.capture.begin_interaction()
        self.driver.add(prefix="partial-")
        self.driver.logs = self.driver.logs[:3]
        self.capture.poll()
        self.assertIsNone(self.capture.month("rf", "2025-08"))

    def test_delayed_pre_action_requests_do_not_satisfy_or_poison_new_selection(self):
        self.capture.begin_interaction(not_before=101)
        self.driver.add(prefix="old-", wall_time=100)
        self.capture.poll()
        self.assertIsNone(self.capture.month("rf", "2025-08"))
        self.assertFalse(self.driver.calls)
        item = exchanges()[0]
        item["request"]["unknownFilter"] = 1
        self.driver.logs = [request(item, "old-invalid", wall_time=100)]
        self.driver.add(prefix="new-", wall_time=102)
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08", generation=1))

    def test_generation_boundary_requires_request_timestamp(self):
        for wall_time in (None, "102", True, float("nan"), float("inf")):
            with self.subTest(wall_time=wall_time):
                driver = Driver()
                capture = MortgageResponseCapture(driver)
                capture.begin_interaction(not_before=101)
                driver.logs = [request(exchanges()[0], "untimed", wall_time=wall_time)]
                capture.poll()
                with self.assertRaisesRegex(ResponseCaptureError, "interaction timestamp"):
                    capture.month("rf", "2025-08")

    def test_initial_navigation_pair_is_available_without_marking_an_interaction(self):
        self.driver.add()
        self.capture.poll()
        self.assertEqual(self.capture.generation, 0)
        self.assertIsNotNone(self.capture.month("rf", "2025-08", generation=0))

    def test_response_body_not_read_before_loading_finished(self):
        self.driver.add(finished=False)
        self.capture.poll()
        self.assertIsNone(self.capture.month("rf", "2025-08"))
        self.assertEqual(self.driver.calls, [])
        self.driver.logs.extend(event("loadingFinished", requestId=str(index)) for index in (0, 1))
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08"))

    def test_partial_pair_waits_without_rounded_fallback(self):
        self.driver.add()
        self.driver.logs = self.driver.logs[:3]
        self.capture.poll()
        self.assertIsNone(self.capture.month("rf", "2025-08"))
        self.assertIsNone(self.capture.month("msk", "2025-08"))
        self.assertIsNone(self.capture.month("rf", "2025-07"))

    def test_initial_navigation_capture_enabled_before_get(self):
        from pipeline.downloaders.nashdom_mortgage import BrowserClient, SOURCE_URL
        driver = Mock()
        order = []
        driver.execute_cdp_cmd.side_effect = lambda name, params: order.append(name)
        driver.get.side_effect = lambda url: order.append(("get", url))
        with patch("pipeline.selenium_utils.create_chrome", return_value=driver) as factory, \
                patch.object(BrowserClient, "_access_check"), patch.object(BrowserClient, "_wait"):
            with BrowserClient("unused") as client:
                self.assertIsInstance(client.responses, MortgageResponseCapture)
            self.assertEqual(order, ["Network.enable", ("get", SOURCE_URL)])
            self.assertTrue(factory.call_args.kwargs["performance_logs"])
            driver.quit.assert_called_once()
            self.assertIsNone(client.responses)

    def test_capture_initialization_failure_closes_browser(self):
        from pipeline.downloaders.nashdom_mortgage import BrowserClient
        driver = Mock()
        driver.execute_cdp_cmd.side_effect = RuntimeError("performance logs unavailable")
        with patch("pipeline.selenium_utils.create_chrome", return_value=driver):
            with self.assertRaises(RuntimeError):
                with BrowserClient("unused"):
                    pass
        driver.get.assert_not_called()
        driver.quit.assert_called_once()

    def test_mismatched_basis_filters_regions_and_urls_ignored(self):
        item = exchanges()[0]
        modifications = [
            {"request": {**item["request"], "periodType": "year"}},
            {"request": {**item["request"], "currency": "RUB"}},
            {"request": {**item["request"], "regionId": True}},
            {"request": {**item["request"], "reportingPeriod": "2025-08-02"}},
            {"request": {**item["request"], "market": "primary"}},
            {"url": item["url"] + "?extra=1"}, {"method": "GET"},
        ]
        for modification in modifications:
            with self.subTest(modification=modification):
                self.driver.logs = [request({**item, **modification}, "unknown")]
                self.capture.poll()
        self.assertFalse(self.capture._scopes)
        self.assertFalse(self.driver.calls)

    def test_missing_invalid_oversized_and_duplicate_payloads_ignored(self):
        item = exchanges()[0]
        for payload in (None, "not JSON", "[]", '"string"', "x" * 4097,
                        '{"regionId":1,"regionId":1,"reportingPeriod":"2025-08-01",'
                        '"periodType":"month","currency":"ALL"}'):
            row = request(item, "unknown")
            envelope = json.loads(row["message"])
            envelope["message"]["params"]["request"]["postData"] = payload
            self.driver.logs = [{"message": json.dumps(envelope)}]
            self.capture.poll()
        self.assertFalse(self.capture._scopes)

    def test_wrong_response_scope_and_non_json_fail(self):
        item = exchanges()[0]
        variants = [{"status": 403}, {"status": 500}, {"status": 200.0},
                    {"status": True}, {"mime": "text/html"}, {"mime": None},
                    {"url": item["url"] + "/redirect"}]
        for variant in variants:
            with self.subTest(variant=variant):
                driver = Driver()
                driver.logs = [request(item, "0"), response(item, "0", **variant)]
                capture = MortgageResponseCapture(driver)
                capture.poll()
                with self.assertRaises(ResponseCaptureError):
                    capture.month("rf", "2025-08")
                self.assertFalse(driver.calls)

    def test_observed_details_500_json_reaches_normalizer_with_raw_failure(self):
        from pipeline.downloaders.nashdom_mortgage import year_bundle
        items = self.driver.add("rf", "2016-10")
        failure = {"status": "INTERNAL_SERVER_ERROR", "code": "500",
                   "title": "Internal Server Error", "timestamp": "22-10-2026 07:50:55",
                   "message": "Source server error; please try later"}
        self.driver.logs[4] = response(items[1], "1", status=500)
        self.driver.bodies["1"] = {"body": json.dumps(failure), "base64Encoded": False}
        self.capture.poll()
        result = self.capture.month("rf", "2016-10")
        self.assertEqual(result["metrics"]["all"]["count"], 85094)
        self.assertIsNone(result["metrics"]["all"]["termMonths"])
        self.assertIsNone(result["metrics"]["all"]["averageAmountRub"])
        raw = result["rawMetrics"]["exchanges"][1]
        self.assertEqual(raw["status"], 500)
        self.assertEqual(json.loads(raw["body"]), failure)
        bundle = year_bundle("rf", 2016, [(10, result)])
        self.assertIsNone(bundle["records"][0]["termMonths"])
        self.assertEqual(len(self.driver.calls), 2)
        self.capture.poll()
        self.assertEqual(len(self.driver.calls), 2)

    def test_other_details_failures_remain_errors_not_fallbacks(self):
        for status, mime, body in (
                (403, "application/json", {"status": "INTERNAL_SERVER_ERROR", "code": "500"}),
                (502, "application/json", {"status": "INTERNAL_SERVER_ERROR", "code": "500"}),
                (500, "text/html", "<html>blocked</html>"),
                (500, "application/json", {"status": "different", "code": "500"}),
                (500, "application/json", {"status": "INTERNAL_SERVER_ERROR", "code": 500}),
                (500, "application/json", {"data": [], "status": "INTERNAL_SERVER_ERROR", "code": "500"})):
            with self.subTest(status=status, mime=mime, body=body):
                driver = Driver()
                items = driver.add()
                driver.logs[4] = response(items[1], "1", status=status, mime=mime)
                driver.bodies["1"] = {"body": json.dumps(body)}
                capture = MortgageResponseCapture(driver)
                capture.poll()
                with self.assertRaises((ResponseCaptureError, ValueError)):
                    capture.month("rf", "2025-08")

    def test_loading_failed_never_reuses_an_older_success(self):
        self.driver.add()
        self.capture.poll()
        item = exchanges()[0]
        self.driver.logs = [request(item, "retry"), event("loadingFailed", requestId="retry")]
        self.capture.poll()
        with self.assertRaises(ResponseCaptureError):
            self.capture.month("rf", "2025-08")

    def test_new_matching_request_invalidates_old_endpoint_immediately(self):
        self.driver.add()
        self.capture.poll()
        item = exchanges()[0]
        self.driver.logs = [request(item, "retry")]
        self.capture.poll()
        self.assertIsNone(self.capture.month("rf", "2025-08"))
        self.driver.logs = [response(item, "retry"), event("loadingFinished", requestId="retry")]
        self.driver.bodies["retry"] = {"body": json.dumps(item["body"])}
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08"))

    def test_redirect_to_unrelated_url_invalidates_original_request(self):
        item = exchanges()[0]
        redirected = {**item, "url": "https://example.test/other"}
        self.driver.logs = [request(item, "0"), request(redirected, "0")]
        self.capture.poll()
        with self.assertRaises(ResponseCaptureError):
            self.capture.month("rf", "2025-08")
        self.assertFalse(self.driver.calls)

    def test_regions_and_months_have_independent_cache_entries(self):
        scopes = [("rf", "2025-08"), ("msk", "2025-08"), ("rf", "2009-01")]
        for index, (region, period) in enumerate(scopes):
            self.driver.add(region, period, prefix=f"{index}-")
        self.capture.poll()
        for region, period in scopes:
            result = self.capture.month(region, period)
            self.assertEqual((result["region"], result["period"]), (region, period))
        result["rawMetrics"]["exchanges"].clear()
        self.assertEqual(len(self.capture.month("rf", "2009-01")["rawMetrics"]["exchanges"]), 2)

    def test_base64_json_and_json_mime_parameters(self):
        self.driver.add()
        for request_id, body in self.driver.bodies.items():
            self.driver.bodies[request_id] = {
                "body": base64.b64encode(body["body"].encode()).decode(), "base64Encoded": True,
            }
        self.capture.poll()
        self.assertEqual(self.capture.month("rf", "2025-08")["metrics"]["all"]["count"], 85094)

    def test_json_mime_with_charset_and_structured_suffix(self):
        for mime in ("application/json; charset=UTF-8", "application/problem+json"):
            driver = Driver()
            items = driver.add()
            driver.logs = []
            for index, item in enumerate(items):
                request_id = str(index)
                driver.logs.extend([request(item, request_id), response(item, request_id, mime=mime),
                                    event("loadingFinished", requestId=request_id)])
            capture = MortgageResponseCapture(driver)
            capture.poll()
            self.assertIsNotNone(capture.month("rf", "2025-08"))

    def test_body_race_retried_on_following_poll_not_new_requests(self):
        self.driver.add()
        self.driver.bodies["0"] = [RuntimeError("not ready"), self.driver.bodies["0"]]
        self.capture.poll()
        self.assertIsNone(self.capture.month("rf", "2025-08"))
        self.capture.poll()
        self.assertIsNotNone(self.capture.month("rf", "2025-08"))
        self.assertEqual(len(self.driver.calls), 3)

    def test_unavailable_body_retries_are_bounded(self):
        self.capture = MortgageResponseCapture(self.driver, body_attempts=2)
        self.driver.add()
        self.driver.bodies["0"] = RuntimeError("not ready")
        self.capture.poll()
        self.capture.poll()
        with self.assertRaises(ResponseCaptureError):
            self.capture.month("rf", "2025-08")
        calls = copy.deepcopy(self.driver.calls)
        self.capture.poll()
        self.assertEqual(self.driver.calls, calls)

    def test_response_size_and_encoding_are_bounded(self):
        bodies = [{"body": "x" * 5000}, {"body": "@@@@", "base64Encoded": True},
                  {"body": base64.b64encode(b"\xff").decode(), "base64Encoded": True},
                  {"body": None}, {"body": "{}", "base64Encoded": "yes"}]
        for body in bodies:
            with self.subTest(body=body):
                driver = Driver()
                driver.add()
                driver.bodies["0"] = body
                capture = MortgageResponseCapture(driver, max_body_bytes=4096)
                capture.poll()
                with self.assertRaises(ResponseCaptureError):
                    capture.month("rf", "2025-08")

    def test_invalid_json_body_rejected_by_normalizer(self):
        self.driver.add()
        self.driver.bodies["0"] = {"body": "<html>not data</html>"}
        self.capture.poll()
        with self.assertRaises(ValueError):
            self.capture.month("rf", "2025-08")

    def test_recent_request_and_scope_cache_limits(self):
        capture = MortgageResponseCapture(self.driver, max_requests=1, max_scopes=2)
        self.driver.add(finished=False)
        capture.poll()
        self.assertLessEqual(len(capture._requests), 1)
        with self.assertRaises(ResponseCaptureError):
            capture.month("rf", "2025-08")
        for index, period in enumerate(("2025-09", "2025-10")):
            self.driver.add(period=period, prefix=f"{index}-")
            capture.poll()
        self.assertLessEqual(len(capture._scopes), 2)
        self.assertIsNone(capture.month("rf", "2025-08"))

    def test_expired_request_fails_without_body_read(self):
        now = [0]
        self.capture = MortgageResponseCapture(self.driver, max_age=10, clock=lambda: now[0])
        self.driver.add(finished=False)
        self.capture.poll()
        now[0] = 11
        self.capture.poll()
        self.assertFalse(self.capture._requests)
        self.assertFalse(self.driver.calls)
        with self.assertRaises(ResponseCaptureError):
            self.capture.month("rf", "2025-08")

    def test_unknown_requests_and_malformed_events_do_not_read_bodies(self):
        self.driver.logs = [None, {}, {"message": "not JSON"}, {"message": "[]"},
                            event("responseReceived", requestId="unknown", response={}),
                            event("loadingFinished", requestId="unknown"),
                            event("responseReceived", requestId=[], response={}),
                            event("loadingFinished", requestId={}),
                            {"message": json.dumps({"message": {"method": "Network.responseReceived", "params": []}})}]
        self.capture.poll()
        self.assertFalse(self.driver.calls)
        self.assertFalse(self.capture._scopes)

    def test_selenium_performance_logging_is_opt_in(self):
        from pipeline.selenium_utils import create_chrome
        for enabled in (False, True):
            driver = Mock()
            with patch("pipeline.selenium_utils.webdriver.Chrome", return_value=driver) as factory:
                result = create_chrome(performance_logs=enabled)
                try:
                    capabilities = factory.call_args.kwargs["options"].to_capabilities()
                    if enabled:
                        self.assertEqual(capabilities["goog:loggingPrefs"], {"performance": "ALL"})
                    else:
                        self.assertNotIn("goog:loggingPrefs", capabilities)
                finally:
                    result.quit()


if __name__ == "__main__":
    unittest.main()
