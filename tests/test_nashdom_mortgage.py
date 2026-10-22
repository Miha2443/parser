"""Offline collector orchestration; fixtures never enter working data."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import Mock, patch

from pipeline.downloaders.nashdom_mortgage import (
    AccessVerificationRequired, BrowserClient, COLLECTOR_VERSION, CollectionIncomplete,
    LEGACY_COLLECTOR_VERSION, RAW_PATH, collect, collection_lock, collection_plan,
    main, require_scope, year_bundle,
)
from pipeline.downloaders.nashdom_mortgage_api import normalize_month
from pipeline.downloaders.nashdom_mortgage_network import ResponseCaptureError
from pipeline.mortgage_history import load_checkpoint, load_published, publish_history, save_checkpoint
from tests.test_nashdom_mortgage_api import exchanges


def monthly(region, year, month, *, value=10, basis="month"):
    period = f"{year}-{month:02d}"
    items = exchanges(region, period)
    for prefix in ("total", "primary", "secondary"):
        items[0]["body"]["data"][prefix + "CreditAmount"] = value * 1e9
        items[0]["body"]["data"][prefix + "CreditCount"] = value * 1000
    result = normalize_month(region, period, items)
    result["basis"] = basis
    return result


def bundle(region, year, *, value=10):
    months = range(1, 4) if year == 2026 else range(1, 13)
    return year_bundle(region, year, [(month, monthly(region, year, month, value=value)) for month in months])


class FakeClient:
    def __init__(self, *, fail=(), available=None, value=10):
        self.fail, self.value = set(fail), value
        self.available = available or {region: [2025, 2026] for region in ("rf", "msk")}
        self.calls = []

    def __call__(self, raw_dir):
        return self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def discover(self):
        return copy.deepcopy(self.available)

    def collect_year(self, region, year):
        self.calls.append((region, year))
        if (region, year) in self.fail:
            raise AccessVerificationRequired("manual verification required")
        return bundle(region, year, value=self.value)


class CollectorTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def test_first_download_all_years_then_only_newest(self):
        first = FakeClient()
        result = collect(self.root, client_factory=first)
        self.assertEqual(set(first.calls), {(region, year) for region in ("rf", "msk") for year in (2025, 2026)})
        self.assertEqual(len(result["records"]), 2 * 15 * 3)
        second = FakeClient(value=11)
        result = collect(self.root, client_factory=second)
        self.assertEqual(set(second.calls), {("rf", 2026), ("msk", 2026)})
        self.assertEqual(next(row for row in result["records"] if row["period"] == "2025-01")["count"], 10000)
        self.assertEqual(next(row for row in result["records"] if row["period"] == "2026-01")["count"], 11000)

    def test_captcha_does_not_erase_previous_history(self):
        collect(self.root, client_factory=FakeClient())
        path = self.root / RAW_PATH / "mortgage_history.json"
        before = path.read_bytes()
        client = FakeClient(fail={("msk", 2026)})
        with self.assertRaises(RuntimeError):
            collect(self.root, client_factory=client)
        self.assertEqual(path.read_bytes(), before)
        self.assertEqual(client.calls, [("msk", 2026)])

    def test_partial_bootstrap_can_publish_both_regions_before_old_year_failure(self):
        client = FakeClient(fail={("msk", 2025)})
        with self.assertRaises(RuntimeError):
            collect(self.root, client_factory=client)
        result = load_published(self.root / RAW_PATH)
        self.assertEqual({row["region"] for row in result["records"]}, {"rf", "msk"})
        self.assertTrue(all(row["period"].startswith("2026-") for row in result["records"]))

    def test_resume_verified_older_checkpoint_without_fetching_it_again(self):
        save_checkpoint(self.root / RAW_PATH, bundle("rf", 2025))
        client = FakeClient()
        collect(self.root, client_factory=client)
        self.assertNotIn(("rf", 2025), client.calls)
        self.assertIn(("msk", 2025), client.calls)
        result = load_published(self.root / RAW_PATH)
        self.assertTrue(any(row["region"] == "rf" and row["period"] == "2025-12" for row in result["records"]))

    def test_force_refreshes_even_verified_checkpoints_and_old_years(self):
        collect(self.root, client_factory=FakeClient())
        client = FakeClient()
        collect(self.root, client_factory=client, force=True)
        self.assertEqual(len(client.calls), 4)

    def test_unknown_region_year_catalog_is_not_accepted(self):
        with self.assertRaises(ValueError):
            collect(self.root, client_factory=FakeClient(available={"rf": [2025]}))
        self.assertIsNone(load_published(self.root / RAW_PATH))

    def test_monthly_basis_and_scope_are_mandatory(self):
        for item in (monthly("rf", 2026, 1, basis="year"), monthly("msk", 2026, 1), monthly("rf", 2025, 1)):
            with self.subTest(item=item):
                with self.assertRaises(ValueError):
                    year_bundle("rf", 2026, [(1, item)])

    def test_literal_units_and_additional_metrics_are_preserved(self):
        item = monthly("rf", 2026, 1)
        item["rawMetrics"]["debt"] = {"value": "3 093", "unit": "billion RUB", "basis": "stock"}
        result = year_bundle("rf", 2026, [(1, item)])
        self.assertEqual(result["records"][0]["volumeRub"], 10e9)
        self.assertEqual(result["records"][0]["sourceEvidence"]["debt"]["basis"], "stock")
        self.assertNotIn("yearToDate", result["records"][0])

    def test_only_matching_monthly_ruble_parameters_enter_monthly_fields(self):
        from pipeline.downloaders.nashdom_mortgage_dom import parse_page
        from tests.test_mortgage_dom import monthly as month_page
        document = month_page()
        for label in document.select(".card_item_body .item_period")[:2]:
            label.string = "за август 2026 года"
        item = parse_page(str(document), expected_region="msk", expected_period="2026-08")
        with self.assertRaisesRegex(ValueError, "exact response"):
            year_bundle("msk", 2026, [(8, item)])
        exact = normalize_month("msk", "2026-08", exchanges("msk", "2026-08"))
        result = year_bundle("msk", 2026, [(8, exact)])
        self.assertEqual(result["records"][0]["termMonths"], 307.000008)
        self.assertIsNone(result["records"][1]["termMonths"])

    def test_exact_response_evidence_and_version_are_required(self):
        item = monthly("rf", 2026, 1)
        for changed in ({**item, "sourcePrecision": "rounded card"},
                        {**item, "rawMetrics": {}},
                        {**item, "metrics": {**item["metrics"], "all": {**item["metrics"]["all"], "count": 1}}}):
            with self.assertRaises(ValueError):
                year_bundle("rf", 2026, [(1, changed)])
        result = year_bundle("rf", 2026, [(1, item)])
        self.assertEqual(result["collector"], COLLECTOR_VERSION)
        self.assertIn("raw response", result["precision"])
        self.assertEqual(result["records"][0]["sourceEvidence"], item["rawMetrics"])

    def legacy_history(self):
        available = {region: [2024, 2025, 2026] for region in ("rf", "msk")}
        for region, years in available.items():
            for year in years:
                item = bundle(region, year)
                item["collector"] = LEGACY_COLLECTOR_VERSION
                save_checkpoint(self.root / RAW_PATH, item)
        return available, publish_history(self.root / RAW_PATH, available)

    def test_legacy_precision_is_refetched_once_not_on_each_run(self):
        available, previous = self.legacy_history()
        self.assertEqual(set(collection_plan(available, previous)),
                         {(region, year) for region in available for year in available[region]})
        first = FakeClient(available=available, value=11)
        collect(self.root, client_factory=first)
        self.assertEqual(len(first.calls), 6)
        second = FakeClient(available=available, value=12)
        result = collect(self.root, client_factory=second)
        self.assertEqual(set(second.calls), {("msk", 2026), ("rf", 2026)})
        self.assertTrue(all(source["collector"] == COLLECTOR_VERSION
                            for years in result["yearSources"].values() for source in years.values()))

    def test_precision_migration_only_extends_known_legacy_scopes(self):
        available, previous = self.legacy_history()
        previous["yearSources"]["rf"]["2024"]["collector"] = COLLECTOR_VERSION
        previous["yearSources"]["msk"]["2024"]["collector"] = "other-verified-collector"
        planned = collection_plan(available, previous)
        self.assertNotIn(("rf", 2024), planned)
        self.assertNotIn(("msk", 2024), planned)
        self.assertIn(("rf", 2025), planned)

    def test_old_checkpoint_cannot_resume_bootstrap(self):
        old = bundle("rf", 2025)
        old["collector"] = LEGACY_COLLECTOR_VERSION
        save_checkpoint(self.root / RAW_PATH, old)
        client = FakeClient()
        collect(self.root, client_factory=client)
        self.assertIn(("rf", 2025), client.calls)

    def test_v2_checkpoint_can_replace_published_legacy_scope(self):
        available, _ = self.legacy_history()
        save_checkpoint(self.root / RAW_PATH, bundle("rf", 2024, value=12))
        client = FakeClient(available=available)
        result = collect(self.root, client_factory=client)
        self.assertNotIn(("rf", 2024), client.calls)
        self.assertEqual(next(row["count"] for row in result["records"]
                              if row["region"] == "rf" and row["period"] == "2024-01"), 12000)

    def test_failed_response_capture_retains_exact_previous_publication(self):
        collect(self.root, client_factory=FakeClient())
        path = self.root / RAW_PATH / "mortgage_history.json"
        before = path.read_bytes()
        client = FakeClient()
        with patch.object(client, "collect_year", side_effect=ResponseCaptureError("response pair absent")):
            with self.assertRaises(RuntimeError):
                collect(self.root, client_factory=client)
        self.assertEqual(path.read_bytes(), before)

    def history_with_parameter_gap(self, *, server_error=True):
        available = {region: [2016, 2026] for region in ("rf", "msk")}
        for region, years in available.items():
            for year in years:
                observations = [(month, monthly(region, year, month))
                                for month in (range(1, 4) if year == 2026 else range(1, 13))]
                if (region, year) == ("rf", 2016):
                    items = observations[9][1]["rawMetrics"]["exchanges"]
                    if server_error:
                        items[1].update(status=500, body={"status": "INTERNAL_SERVER_ERROR", "code": "500"})
                    else:
                        items[1]["body"]["data"][0].update(creditAvgPeriod=None, creditAmountAvg=None)
                    observations[9] = (10, normalize_month(region, "2016-10", items))
                save_checkpoint(self.root / RAW_PATH, year_bundle(region, year, observations))
        return available, publish_history(self.root / RAW_PATH, available)

    def test_historical_details_server_error_is_refetched_then_stops_after_repair(self):
        available, previous = self.history_with_parameter_gap()
        self.assertEqual(set(collection_plan(available, previous)),
                         {("rf", 2016), ("rf", 2026), ("msk", 2026)})
        client = FakeClient(available=available)
        repaired = collect(self.root, client_factory=client)
        self.assertIn(("rf", 2016), client.calls)
        self.assertNotIn(("msk", 2016), client.calls)
        self.assertEqual(set(collection_plan(available, repaired)), {("rf", 2026), ("msk", 2026)})
        row = next(item for item in repaired["records"] if item["region"] == "rf"
                   and item["period"] == "2016-10" and item["segment"] == "all")
        self.assertIsNotNone(row["termMonths"])

    def test_normal_source_null_parameters_and_segment_nulls_are_not_failures(self):
        available, previous = self.history_with_parameter_gap(server_error=False)
        self.assertEqual(set(collection_plan(available, previous)), {("rf", 2026), ("msk", 2026)})
        self.assertIsNotNone(collect(self.root, client_factory=FakeClient(available=available)))

    def test_complete_calendar_with_details_error_is_published_but_reported_partial(self):
        available, _ = self.history_with_parameter_gap()
        failure = load_checkpoint(self.root / RAW_PATH, "rf", 2016)
        (self.root / RAW_PATH / "mortgage_history.json").unlink()
        client = FakeClient(available=available)
        original = client.collect_year

        def fetch(region, year):
            item = original(region, year)
            return copy.deepcopy(failure) if (region, year) == ("rf", 2016) else item

        with patch.object(client, "collect_year", side_effect=fetch):
            with self.assertRaisesRegex(CollectionIncomplete, "parameters remain unavailable") as caught:
                collect(self.root, client_factory=client)
        self.assertTrue(caught.exception.published)
        result = load_published(self.root / RAW_PATH)
        self.assertEqual(len(result["records"]), 2 * 15 * 3)
        self.assertEqual(client.calls.count(("rf", 2016)), 1)
        self.assertIn(("rf", 2016), collection_plan(available, result))

    def test_cli_uses_exit_two_for_published_parameter_gaps(self):
        with patch("pipeline.downloaders.nashdom_mortgage.collect", side_effect=CollectionIncomplete(
                "parameters remain unavailable", published=True)), patch("sys.stderr") as stderr:
            self.assertEqual(main(["--root", str(self.root)]), 2)
        self.assertIn("parameters remain unavailable", "".join(
            call.args[0] for call in stderr.write.call_args_list))

    def test_unverified_error_evidence_is_not_a_verified_details_failure(self):
        from pipeline.downloaders.nashdom_mortgage import details_failure_scopes
        _, previous = self.history_with_parameter_gap()
        for row in previous["records"]:
            if row["period"] == "2016-10" and row["region"] == "rf":
                row["sourceEvidence"]["exchanges"][1]["request"]["unknownFilter"] = 1
        self.assertEqual(details_failure_scopes(previous), set())

    def test_temporary_details_failure_checkpoint_is_not_resumed_as_complete(self):
        available, _ = self.history_with_parameter_gap()
        (self.root / RAW_PATH / "mortgage_history.json").unlink()
        client = FakeClient(available=available)
        collect(self.root, client_factory=client)
        self.assertIn(("rf", 2016), client.calls)
        self.assertNotIn(("msk", 2016), client.calls)

    def test_source_scope_must_not_silently_become_a_subset(self):
        meta = {"filters": {"currency": "ALL", "market": "total", "housing": "all"},
                "selection": {"includeAutonomous": "yes", "creditOrganizations": []}}
        require_scope(meta)
        for key, value in (("currency", "RUB"), ("market", "primary"), ("housing", "private")):
            changed = copy.deepcopy(meta)
            changed["filters"][key] = value
            with self.assertRaises(RuntimeError):
                require_scope(changed)

    def test_os_lock_released_after_error(self):
        raw = self.root / RAW_PATH
        with self.assertRaisesRegex(RuntimeError, "test"):
            with collection_lock(raw):
                raise RuntimeError("test")
        with collection_lock(raw):
            self.assertTrue((raw / ".mortgage_history.lock").exists())


class BrowserWaitTests(unittest.TestCase):
    def setUp(self):
        from tests.test_mortgage_dom import monthly as month_page
        self.html = str(month_page())
        self.client = BrowserClient("unused")
        self.client.driver = Mock()
        self.client._interaction = 1
        self.client.responses = Mock()
        self.client.responses.month.side_effect = lambda region, period, **kwargs: normalize_month(
            region, period, exchanges(region, period))
        self.client._access_check = Mock()
        self.client._html = Mock(side_effect=lambda: self.html)
        self.client._network = Mock(return_value={"pending": 0, "idle": 800,
            "finished": [{"url": item["url"], "status": 200} for item in exchanges()]})

        class Wait:
            def __init__(wait, driver, *args, **kwargs):
                wait.driver = driver

            def until(wait, callback, message=""):
                from selenium.common.exceptions import TimeoutException
                for _ in range(4):
                    result = callback(wait.driver)
                    if result:
                        return result
                raise TimeoutException(message)

        self.enterContext(patch("selenium.webdriver.support.ui.WebDriverWait", Wait))

    def test_stable_render_supplies_identity_but_not_rounded_values(self):
        meta, item = self.client._wait(region="msk", period="2026-08", basis="month", requested=True)
        self.assertEqual(meta["basis"], "month")
        self.assertEqual(item["metrics"]["all"]["count"], 85094)
        self.assertEqual(item["metrics"]["all"]["volumeRub"], 392274000000)
        self.assertEqual(item["rawMetrics"]["renderedPage"]["current"]["all"]["count"]["value"], 48116)
        self.assertTrue(item["selectionEvidence"]["responseCapture"])
        self.assertEqual(self.client.responses.poll.call_count, 3)

    def test_initial_month_without_explicit_basis_still_uses_exact_response(self):
        self.client._interaction = 0
        _, item = self.client._wait()
        self.assertEqual(item["sourcePrecision"], "raw response")
        self.client.responses.month.assert_called_with("msk", "2026-08", generation=0)

    def test_missing_pair_times_out_without_using_dom_metrics(self):
        from selenium.common.exceptions import TimeoutException
        self.client.responses.month.side_effect = None
        self.client.responses.month.return_value = None
        with self.assertRaisesRegex(TimeoutException, "exact monthly responses"):
            self.client._wait(region="msk", period="2026-08", basis="month")
        self.assertEqual(self.client.responses.poll.call_count, 4)

    def test_failed_exact_response_does_not_fall_back_to_cards(self):
        self.client.responses.month.side_effect = ResponseCaptureError("non-JSON content")
        with self.assertRaisesRegex(ResponseCaptureError, "non-JSON"):
            self.client._wait(region="msk", period="2026-08", basis="month")

    def test_verified_details_500_does_not_discard_valid_general_metrics(self):
        from pipeline.downloaders.nashdom_mortgage_api import ENDPOINTS
        items = exchanges("msk", "2026-08")
        items[1].update(status=500, body={"status": "INTERNAL_SERVER_ERROR", "code": "500"})
        self.client.responses.month.side_effect = lambda region, period, **kwargs: normalize_month(region, period, items)
        self.client._network.return_value["finished"][1] = {"url": ENDPOINTS["details"], "status": 500}
        _, item = self.client._wait(region="msk", period="2026-08", basis="month", requested=True)
        self.assertEqual(item["metrics"]["all"]["count"], 85094)
        self.assertIsNone(item["metrics"]["all"]["termMonths"])
        self.assertIsNone(item["metrics"]["all"]["averageAmountRub"])
        self.assertEqual(item["selectionEvidence"]["completedRequests"][1]["status"], 500)

    def test_other_ui_request_failures_are_not_ignored(self):
        from pipeline.downloaders.nashdom_mortgage_api import ENDPOINTS
        for url, status in ((ENDPOINTS["general"], 500), (ENDPOINTS["details"], 403),
                            (ENDPOINTS["details"], 502), (ENDPOINTS["details"] + "?x=1", 500),
                            (ENDPOINTS["details"], 500.0)):
            with self.subTest(url=url, status=status):
                self.client._network.return_value["finished"] = [{"url": url, "status": status}]
                with self.assertRaisesRegex(RuntimeError, "request failed"):
                    self.client._wait()
        self.client.responses.month.assert_not_called()

    def test_details_failure_does_not_reuse_a_cached_success_or_wrong_basis(self):
        from pipeline.downloaders.nashdom_mortgage_api import ENDPOINTS
        from tests.test_mortgage_dom import soup
        self.client._network.return_value["finished"] = [{"url": ENDPOINTS["details"], "status": 500}]
        with self.assertRaisesRegex(RuntimeError, "no matching exact response"):
            self.client._wait()
        self.html = str(soup())
        with self.assertRaisesRegex(RuntimeError, "not a verified monthly"):
            self.client._wait()

    def test_scope_must_match_dom_before_matching_responses_are_used(self):
        from selenium.common.exceptions import TimeoutException
        with self.assertRaises(TimeoutException):
            self.client._wait(region="rf", period="2026-08", basis="month")
        self.client.responses.month.assert_not_called()

    def test_initial_year_basis_can_be_discovered_but_cannot_be_published(self):
        from tests.test_mortgage_dom import soup
        self.html = str(soup())
        meta, item = self.client._wait()
        self.assertEqual(meta["basis"], "year")
        self.client.responses.month.assert_not_called()
        with self.assertRaisesRegex(ValueError, "basis mismatch"):
            year_bundle("msk", 2026, [(8, item)])

    def test_capture_is_drained_while_ui_requests_are_pending(self):
        from selenium.common.exceptions import TimeoutException
        self.client._network.return_value["pending"] = 2
        with self.assertRaises(TimeoutException):
            self.client._wait()
        self.assertEqual(self.client.responses.poll.call_count, 4)
        self.client.responses.month.assert_not_called()

    def test_requested_wait_requires_a_marked_interaction(self):
        self.client._interaction = 0
        with self.assertRaisesRegex(ResponseCaptureError, "not marked"):
            self.client._wait(requested=True)
        self.client.responses.month.assert_not_called()

    def real_capture(self):
        from tests.test_nashdom_mortgage_network import Driver
        from pipeline.downloaders.nashdom_mortgage_network import MortgageResponseCapture
        driver = Driver()
        self.client.responses = MortgageResponseCapture(driver)
        self.client._interaction = 0
        self.client.driver.execute_script.return_value = 101
        driver.add("msk", "2026-08", wall_time=100)
        self.client._wait()
        return driver

    def test_review_repro_unknown_filtered_post_cannot_confirm_cached_month(self):
        from tests.test_nashdom_mortgage_network import request, response, event
        driver = self.real_capture()
        self.client._mark()
        item = exchanges("msk", "2026-08")[0]
        item["request"]["unknownFilter"] = "subset"
        driver.logs = [request(item, "unexpected", wall_time=102), response(item, "unexpected"),
                       event("loadingFinished", requestId="unexpected")]
        with self.assertRaisesRegex(ResponseCaptureError, "Unexpected mortgage request"):
            self.client._wait(region="msk", period="2026-08", basis="month", requested=True)

    def test_new_selection_without_new_matching_pair_cannot_return_previous_pair(self):
        from selenium.common.exceptions import TimeoutException
        self.real_capture()
        self.client._mark()
        with self.assertRaises(TimeoutException):
            self.client._wait(region="msk", period="2026-08", basis="month", requested=True)

    def test_marked_action_accepts_only_new_pair_and_records_its_generation(self):
        driver = self.real_capture()
        self.client._mark()
        driver.add("msk", "2026-08", prefix="new-", wall_time=102)
        _, item = self.client._wait(region="msk", period="2026-08", basis="month", requested=True)
        self.assertEqual(item["selectionEvidence"]["interactionGeneration"], 1)
        self.assertEqual(self.client.responses.generation, 1)


if __name__ == "__main__":
    unittest.main()
