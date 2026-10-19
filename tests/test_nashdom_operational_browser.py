"""Offline evidence, portable recovery, and ordinary-page browser regressions."""
from copy import deepcopy
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import textwrap
import unittest
from unittest.mock import Mock, call, patch

from selenium.common.exceptions import TimeoutException

import nashdom_checker as nc
from pipeline.downloaders import nashdom_operational_api as api
from pipeline.downloaders import nashdom_operational_browser as browser
from pipeline.downloaders import nashdom_operational_contract as contract
from pipeline.downloaders import nashdom_operational_history as history
from scripts.check_nashdom_operational_history import FakeSource, fixture


STAMP = "2026-10-19T12:00:00+00:00"
AREA_UNIT = "\u0442\u044b\u0441. \u043c\u00b2"
RUB_UNIT = "\u0440\u0443\u0431."
FUNDS_UNIT = "\u043c\u043b\u043d \u0440\u0443\u0431."


def completed_request(block, month, region):
    return {"url": api.BASE + api.request_url(block, month, region), "status": 200}


def page_snapshot(block, month, region, kind=None):
    year, num = map(int, month.split("-"))
    result = {"region": browser.REGIONS[region], "report_period": f"{api.MONTHS[num - 1]} {year}",
              "report_date": ""}
    if block == "construction":
        value = {("rf", "living"): 102849, ("rf", "total"): 162274,
                 ("msk", "living"): 15680, ("msk", "total"): 26418}[region, kind]
        return {**result, "area_kind": kind, "area_thousand_m2": f"{value:,}".replace(",", " ") + " " + AREA_UNIT}
    values = ({"total_living_thousand_m2": 102849, "sales_open_thousand_m2": 78320,
               "sold_thousand_m2": 42722, "unsold_thousand_m2": 35598, "sales_not_open_thousand_m2": 24530,
               "sold_pct": 42, "unsold_pct": 35, "sales_not_open_pct": 24,
               "price_per_m2_rub": 78333, "funds_million_rub": 3346546} if region == "rf" else
              {"total_living_thousand_m2": 1000, "sales_open_thousand_m2": 800,
               "sold_thousand_m2": 421, "unsold_thousand_m2": 379, "sales_not_open_thousand_m2": 200,
               "sold_pct": 42, "unsold_pct": 38, "sales_not_open_pct": 20,
               "price_per_m2_rub": 215333, "funds_million_rub": 90655})
    result["metrics"] = {
        field: str(value) + " " + ("%" if field.endswith("_pct") else FUNDS_UNIT if field == "funds_million_rub"
                                   else RUB_UNIT if field.endswith("_rub") else AREA_UNIT)
        for field, value in values.items()
    }
    return result


def observations(block, month):
    return [{"url": browser.page_url(block), "region_key": region,
             **({"area_kind": kind} if kind else {}), "requested_period": month,
             "snapshot": page_snapshot(block, month, region, kind),
             "selection": {"kind": "period_control", "selected_period": month, "render_stable": True,
                           "pending_requests": 0, "completed_requests": [completed_request(block, month, region)]}}
            for region in browser.REGIONS
            for kind in (("living", "total") if block == "construction" else (None,))]


def browser_artifact(block, month="2020-01", *, stamp=STAMP):
    evidence = observations(block, month)
    return {"schemaVersion": 1, "evidence_kind": "browser_page", "block": block, "period": month,
            "collected_at": stamp, "observations": evidence,
            "rows": browser.normalize_page(block, month, evidence, collected_at=stamp)}


class OfflineTests(unittest.TestCase):
    def setUp(self):
        self.http = self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("live network forbidden")))
        self.chrome = self.enterContext(patch.object(nc, "create_chrome", side_effect=AssertionError("live browser forbidden")))
        self.api_client = self.enterContext(patch.object(api, "OfficialClient", side_effect=AssertionError("API client forbidden")))


class BrowserArtifactTests(OfflineTests):
    def test_api_reexports_identical_pure_contract_errors_and_normalizers(self):
        for name in ("ReportError", "TransportError", "AccessVerificationRequired", "EmptyReport",
                     "period", "numeric", "normalize", "source_row", "request_url", "utc_now"):
            with self.subTest(name=name):
                self.assertIs(getattr(api, name), getattr(contract, name))
        self.assertIs(browser.ReportError, contract.ReportError)
        self.assertIs(history.ReportError, contract.ReportError)

    def test_portable_page_artifacts_validate_without_collector_dependencies(self):
        program = textwrap.dedent("""
            import importlib.abc
            import json
            import sys

            class NoCollectorDependencies(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if fullname.split('.')[0] in {'selenium', 'bs4', 'nashdom_checker'} or fullname == 'pipeline.downloaders.nashdom_operational_api':
                        raise ModuleNotFoundError('collector dependency forbidden: ' + fullname)

            sys.meta_path.insert(0, NoCollectorDependencies())
            from pipeline.downloaders import nashdom_operational_contract as contract
            from pipeline.downloaders import nashdom_operational_browser as browser
            from pipeline.downloaders import nashdom_operational_history as history
            for artifact in json.load(sys.stdin):
                block, month = history.validate_artifact(artifact)
                recovered = history.bundle_entries({block: artifact['rows']}, block)
                assert recovered[month]['rows'] == artifact['rows']
            assert not any(name.split('.')[0] in {'selenium', 'bs4', 'nashdom_checker'} for name in sys.modules)
            assert 'pipeline.downloaders.nashdom_operational_api' not in sys.modules
            print('pure page validation: ok')
        """)
        result = subprocess.run([sys.executable, "-c", program], cwd=Path(__file__).resolve().parents[1],
                                input=json.dumps([browser_artifact(block) for block in ("construction", "sales")]),
                                capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(result.stdout.strip(), "pure page validation: ok")

    def test_artifacts_keep_exact_page_period_region_mode_and_literal_evidence(self):
        with patch.object(api, "normalize", side_effect=AssertionError("API responses must not be fabricated")):
            for block, count in (("construction", 4), ("sales", 2)):
                with self.subTest(block=block):
                    item = browser_artifact(block)
                    self.assertEqual(history.validate_artifact(item), (block, "2020-01"))
                    self.assertEqual(len(item["rows"]), count)
                    self.assertNotIn("responses", item)
                    for row, proof in zip(item["rows"], item["observations"]):
                        self.assertEqual(row["source_url"], browser.page_url(block))
                        self.assertEqual(row["region"], browser.REGIONS[row["region_key"]])
                        self.assertEqual(row["report_period"], "\u042f\u043d\u0432\u0430\u0440\u044c 2020")
                        self.assertEqual(row["page_evidence"], proof)
                        self.assertEqual(row["evidence_kind"], "browser_page")
                        self.assertTrue(row["period_verified"])
                        self.assertNotIn("period_evidence", row)
                        self.assertEqual(row["report_date"], "")
                        self.assertEqual(set(proof["selection"]["completed_requests"][0]), {"url", "status"})
        self.http.assert_not_called()

    def test_mismatched_page_selection_region_and_area_mode_are_rejected(self):
        mutations = (
            lambda item: item.update(url=browser.page_url("sales")),
            lambda item: item.update(url=browser.page_url("construction") + "?repMonth=1"),
            lambda item: item.update(requested_period="2020-02"),
            lambda item: item["snapshot"].update(report_period="2020-02"),
            lambda item: item["snapshot"].update(region=browser.REGIONS["msk"]),
            lambda item: item["snapshot"].update(area_kind="total"),
            lambda item: item["selection"].update(selected_period="2020-02"),
            lambda item: item.update(region_key="other"),
        )
        for index, mutate in enumerate(mutations):
            with self.subTest(change=index):
                proof = observations("construction", "2020-01")
                mutate(proof[0])
                with self.assertRaises(api.ReportError):
                    browser.normalize_page("construction", "2020-01", proof, collected_at=STAMP)
        for proof in (observations("construction", "2020-01")[:-1],
                      [observations("construction", "2020-01")[0]] * 4):
            with self.assertRaises(api.ReportError):
                browser.normalize_page("construction", "2020-01", proof, collected_at=STAMP)

    def test_only_completed_own_page_requests_match_explicit_period_and_moscow(self):
        for block in ("construction", "sales"):
            good = completed_request(block, "2020-01", "msk")
            self.assertTrue(browser.matching_request(good, block, "2020-01", "msk"))
            bad_urls = (
                good["url"].replace(api.BASE, "https://example.invalid"),
                completed_request("sales" if block == "construction" else "construction", "2020-01", "msk")["url"],
                good["url"].replace("repYear=2020", "repYear=2021"),
                good["url"].replace("repMonth=1", "repMonth=2"),
                good["url"].replace("repMonth=1", "unused=1"),
                good["url"].replace("regionCode=77", "regionCode=50"),
                completed_request(block, "2020-01", "rf")["url"],
                good["url"] + "&repYear=2021",
            )
            for url in bad_urls:
                with self.subTest(block=block, url=url):
                    self.assertFalse(browser.matching_request({"url": url, "status": 200}, block, "2020-01", "msk"))
            for status in (None, 0, 202, 204, 302, 403, 500, "200"):
                self.assertFalse(browser.matching_request({**good, "status": status}, block, "2020-01", "msk"))
            self.assertFalse(browser.matching_request(good, block, "2020-01", "rf"))
            rf = completed_request(block, "2020-01", "rf")
            self.assertTrue(browser.matching_request(rf, block, "2020-01", "rf"))
            self.assertTrue(browser.matching_request({**rf, "url": rf["url"] + "&regionCode=all"}, block, "2020-01", "rf"))

    def test_stale_labels_missing_requests_pending_or_unstable_render_are_refused(self):
        for change in ({"completed_requests": []}, {"completed_requests": [{"status": 0}]},
                       {"pending_requests": 1}, {"render_stable": False}, {"render_stable": None},
                       {"selected_period": "2020-02"}, {"kind": "label_only"}):
            with self.subTest(change=change):
                proof = observations("sales", "2020-01")
                proof[1]["selection"].update(change)
                with self.assertRaises(api.ReportError):
                    browser.normalize_page("sales", "2020-01", proof, collected_at=STAMP)
        proof = observations("sales", "2020-01")
        proof[1]["selection"]["completed_requests"] = [completed_request("sales", "2020-01", "rf")]
        with self.assertRaises(api.ReportError):
            browser.normalize_page("sales", "2020-01", proof, collected_at=STAMP)

    def test_initial_page_proves_only_the_observed_latest_month(self):
        proof = observations("construction", "2020-01")
        for item in proof:
            item["selection"] = {"kind": "initial_page", "latest_period": "2020-01", "selected_period": "2020-01",
                                 "pending_requests": 0, "render_stable": True}
        self.assertEqual(len(browser.normalize_page("construction", "2020-01", proof, collected_at=STAMP)), 4)
        proof[-1]["selection"]["latest_period"] = "2020-02"
        with self.assertRaises(api.ReportError):
            browser.normalize_page("construction", "2020-01", proof, collected_at=STAMP)

    def test_historical_report_dates_distinguish_changed_date_from_global_latest(self):
        for block, rendered_date, initial_date, expected_date in (
            ("construction", "31.01.2020", "19.10.2026", "31.01.2020"),
            ("sales", "11.10.2026", "11.10.2026", ""),
            ("construction", "31.01.2020", "", ""),
            ("sales", "31.01.2020", None, ""),
        ):
            with self.subTest(block=block, initial_date=initial_date):
                proof = observations(block, "2020-01")
                for item in proof:
                    item["snapshot"]["report_date"] = rendered_date
                    item["selection"]["initial_report_date"] = initial_date
                original = deepcopy(proof)
                rows = browser.normalize_page(block, "2020-01", proof, collected_at=STAMP)
                self.assertEqual({row["report_date"] for row in rows}, {expected_date})
                self.assertEqual({row["page_evidence"]["snapshot"]["report_date"] for row in rows}, {rendered_date})
                self.assertEqual(proof, original)
                artifact = {"schemaVersion": 1, "evidence_kind": "browser_page", "block": block, "period": "2020-01",
                            "collected_at": STAMP, "observations": proof, "rows": rows}
                self.assertEqual(history.validate_artifact(artifact), (block, "2020-01"))
                self.assertEqual(history.bundle_entries({block: rows}, block)["2020-01"]["rows"], rows)

    def test_latest_initial_page_date_is_kept_without_a_historical_claim(self):
        proof = observations("sales", "2026-10")
        for item in proof:
            item["snapshot"]["report_date"] = "11.10.2026"
            item["selection"] = {"kind": "initial_page", "latest_period": "2026-10", "selected_period": "2026-10",
                                 "pending_requests": 0, "render_stable": True}
        rows = browser.normalize_page("sales", "2026-10", proof, collected_at=STAMP)
        self.assertEqual({row["report_date"] for row in rows}, {"11.10.2026"})

    def test_page_numbers_accept_literal_units_decimal_separators_and_zero(self):
        for value, field, expected in (
            ("1 234,5 " + AREA_UNIT, "area_thousand_m2", 1234.5),
            ("1\u00a0234\u202f567.5 " + RUB_UNIT, "price_per_m2_rub", 1234567.5),
            ("2 345 " + FUNDS_UNIT, "funds_million_rub", 2345),
            ("0 %", "sold_pct", 0), (0, "sold_thousand_m2", 0), (12.5, "area_thousand_m2", 12.5),
        ):
            with self.subTest(value=value, field=field):
                self.assertEqual(browser.page_number(value, field), expected)

    def test_arbitrary_units_malformed_negative_and_nonfinite_numbers_are_refused(self):
        for value in ("12 kg", "12 %", "12 " + RUB_UNIT, "12 million m2", "1e3", "1.2.3", "-1", "+1",
                      "NaN", "inf", "value 12", True, False, float("nan"), float("inf"), -1, {}, "9" * 400):
            with self.subTest(value=value), self.assertRaises(api.ReportError):
                browser.page_number(value, "area_thousand_m2")
        with self.assertRaises(api.ReportError):
            browser.page_number("42 " + AREA_UNIT, "sold_pct")

    def test_optional_null_and_measured_zero_are_preserved_without_deriving_areas(self):
        for field in api.OPTIONAL_SALES:
            for value in (None, "", "-", "\u2014"):
                self.assertIsNone(browser.page_number(value, field))
            self.assertEqual(browser.page_number(0, field), 0)
        for value in (None, "", "-", "\u2014"):
            with self.assertRaises(api.ReportError):
                browser.page_number(value, "sold_thousand_m2")
        proof = observations("sales", "2020-01")
        for item in proof:
            item["snapshot"]["metrics"].update({field: None for field in api.OPTIONAL_SALES})
        rows = browser.normalize_page("sales", "2020-01", proof, collected_at=STAMP)
        self.assertEqual(rows[0]["metrics"]["sold_thousand_m2"], 42722)
        self.assertTrue(all(rows[0]["metrics"][field] is None for field in api.OPTIONAL_SALES))
        proof[0]["snapshot"]["metrics"].update(sold_thousand_m2="0", unsold_thousand_m2="78320",
                                               sold_pct="0%", price_per_m2_rub="0", funds_million_rub="0")
        zero = browser.normalize_page("sales", "2020-01", proof, collected_at=STAMP)[0]["metrics"]
        self.assertEqual([zero[field] for field in ("sold_thousand_m2", "sold_pct", "price_per_m2_rub", "funds_million_rub")],
                         [0, 0, 0, 0])

    def test_sales_keep_raw_values_not_total_times_rounded_percentages(self):
        rows = browser_artifact("sales")["rows"]
        self.assertEqual(rows[0]["metrics"]["sold_thousand_m2"], 42722)
        self.assertNotEqual(rows[0]["metrics"]["sold_thousand_m2"], 102849 * .42)
        self.assertEqual(rows[1]["metrics"]["sold_thousand_m2"], 421)
        self.assertNotEqual(rows[1]["metrics"]["sold_thousand_m2"], 1000 * .42)
        proof = observations("sales", "2020-01")
        del proof[0]["snapshot"]["metrics"]["sold_thousand_m2"]
        with self.assertRaises(api.ReportError):
            browser.normalize_page("sales", "2020-01", proof, collected_at=STAMP)

    def test_artifact_union_rejects_fabricated_mixed_or_tampered_evidence(self):
        item = browser_artifact("construction")
        item["responses"] = fixture("construction", "2020-01")["responses"]
        with self.assertRaisesRegex(api.ReportError, "mixed"):
            history.validate_artifact(item)
        item = fixture("sales", "2020-01")
        item["observations"] = observations("sales", "2020-01")
        with self.assertRaisesRegex(api.ReportError, "mixed"):
            history.validate_artifact(item)
        item = browser_artifact("construction")
        item["rows"][0]["area_thousand_m2"] += 1
        with self.assertRaises(api.ReportError):
            history.validate_artifact(item)

    def test_mixed_api_browser_store_recovers_from_portable_bundle_alone(self):
        with tempfile.TemporaryDirectory() as tmp, tempfile.TemporaryDirectory() as moved:
            store = history.HistoryStore(tmp)
            for block in ("construction", "sales"):
                store.accept(fixture(block, "2020-01", stamp=STAMP))
                store.accept(browser_artifact(block, "2020-02"))
            bounds = {"construction": "2020-02", "sales": "2020-02"}
            store.publish(store.coverage(bounds), complete=True)
            portable = Path(moved) / history.BUNDLE_NAME
            portable.write_bytes(store.bundle.read_bytes())
            restored = history.HistoryStore(moved)
            self.assertEqual(restored.recovery_issues, [])
            self.assertFalse((restored.private / "observations").exists())
            for block in bounds:
                self.assertEqual(restored.rows(block), store.rows(block))
                self.assertEqual(restored.coverage(bounds)[block]["missing_months"], [])
            payload = json.loads(portable.read_text(encoding="utf-8"))
            self.assertEqual(set(history.validate_bundle_payload(payload)["construction"]), {"2020-01", "2020-02"})

    def test_portable_browser_evidence_must_still_match_captured_cells_and_selection(self):
        for mutation in ("value", "request", "period", "source", "verified"):
            with self.subTest(mutation=mutation):
                item = browser_artifact("construction")
                row = item["rows"][0]
                if mutation == "value":
                    row["area_thousand_m2"] += 1
                elif mutation == "request":
                    row["page_evidence"]["selection"]["completed_requests"] = []
                elif mutation == "period":
                    row["page_evidence"]["snapshot"]["report_period"] = "2020-02"
                elif mutation == "source":
                    row["source_url"] = api.BASE + api.request_url("construction", "2020-01", "rf")
                else:
                    row["period_verified"] = False
                with self.assertRaises(api.ReportError):
                    history.bundle_entries({"construction": item["rows"]}, "construction")
        mixed = browser_artifact("construction")["rows"]
        mixed[0] = fixture("construction", "2020-01", stamp=STAMP)["rows"][0]
        with self.assertRaisesRegex(api.ReportError, "mixed"):
            history.bundle_entries({"construction": mixed}, "construction")

    def test_browser_checkpoint_recovers_without_published_bundle(self):
        with tempfile.TemporaryDirectory() as tmp:
            item = browser_artifact("sales")
            store = history.HistoryStore(tmp)
            path = store.accept(item)
            self.assertTrue(path.exists())
            restored = history.HistoryStore(tmp)
            self.assertEqual(restored.rows("sales"), item["rows"])
            self.assertEqual(restored.recovery_issues, [])

    def test_downloader_rejects_valid_artifact_for_wrong_scheduled_block_or_month(self):
        for mismatch in ("block", "period"):
            class WrongJob(FakeSource):
                def collect(self, block, month):
                    self.calls.append((block, month))
                    return browser_artifact("sales" if block == "construction" else "construction", month) if mismatch == "block" else browser_artifact(block, "2020-02")

            with self.subTest(mismatch=mismatch), tempfile.TemporaryDirectory() as tmp:
                source = WrongJob(bounds={"construction": "2020-01", "sales": "2020-01"})
                result = history.download_history(tmp, client=source, progress=lambda _: None)
                self.assertFalse(result["complete"])
                self.assertEqual((result["attempted"], result["collected"]), (2, 0))
                self.assertEqual(len(result["errors"]), 2)
                self.assertEqual(result["new_files"], [])
                self.assertEqual(history.HistoryStore(tmp).entries, {"construction": {}, "sales": {}})

    def test_cli_returns_two_for_partial_and_zero_for_complete(self):
        with tempfile.TemporaryDirectory() as tmp:
            for complete, expected in ((False, 2), (True, 0)):
                with self.subTest(complete=complete), patch.object(history, "download_history", return_value={"complete": complete}) as download:
                    self.assertEqual(history.main(["--raw-dir", tmp, "--max-periods", "1", "--delay", "0"]), expected)
                    download.assert_called_once_with(Path(tmp), start="2020-01", end=None,
                                                     recent=3, max_periods=1, delay=0)
        self.chrome.assert_not_called()
        self.http.assert_not_called()

    def test_verify_published_attempt_accepts_new_validated_partial(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = FakeSource(bounds={"construction": "2020-01", "sales": "2020-01"})
            source.collect = Mock(side_effect=browser_artifact)
            result = history.download_history(tmp, client=source, max_periods=1, progress=lambda _: None)
            self.assertFalse(result["complete"])
            self.assertTrue(result["published"])
            self.assertEqual(result["collected"], 1)
            attempt = history.verify_published_attempt(tmp)
            self.assertEqual((attempt["run_status"], attempt["collected"], attempt["published"]), ("partial", 1, True))
            self.assertFalse(attempt["complete"])
            payload = json.loads(Path(result["history_file"]).read_text(encoding="utf-8"))
            history.validate_bundle_payload(payload)
            with patch.object(history, "download_history", side_effect=AssertionError("verification must be offline")) as download, patch.object(history, "emit"):
                self.assertEqual(history.main(["--raw-dir", tmp, "--verify-published-attempt"]), 0)
            download.assert_not_called()
            self.assertEqual(result["current_file"], "")
            self.assertFalse(list(Path(tmp).glob("construction_operational_*.json")))

    def test_verify_published_attempt_rejects_unpublished_hash_mismatch_or_no_new_records(self):
        with tempfile.TemporaryDirectory() as tmp:
            source = FakeSource(bounds={"construction": "2020-01", "sales": "2020-01"})
            source.collect = Mock(side_effect=browser_artifact)
            history.download_history(tmp, client=source, max_periods=1, progress=lambda _: None)
            path = Path(tmp) / ".construction_history" / "last_attempt.json"
            original = json.loads(path.read_text(encoding="utf-8"))
            for change, message in (({"published": False}, "no new verified history bundle"),
                                    ({"collected": 0}, "no new verified history bundle"),
                                    ({"bundle_sha256": "0" * 64}, "differs from the last collection attempt")):
                with self.subTest(change=change):
                    history.atomic_json(path, {**original, **change})
                    with self.assertRaisesRegex(contract.ReportError, message):
                        history.verify_published_attempt(tmp)


class PollingWait:
    def __init__(self, driver, timeout, poll_frequency=0.25):
        self.driver = driver

    def until(self, predicate):
        for _ in range(9):
            result = predicate(self.driver)
            if result:
                return result
        raise TimeoutException("offline polling limit")


class BrowserClientTests(OfflineTests):
    def setUp(self):
        super().setUp()
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.driver = Mock(name="ordinary_page")
        self.driver.execute_script.side_effect = self.execute_script
        self.chrome.side_effect = None
        self.chrome.return_value = self.driver
        self.bounds = {"construction": "2020-03", "sales": "2020-02"}
        self.network_states = []
        self.network = {"pending": 0, "idle": 500, "finished": []}
        self.access = {"denied": False, "captcha": False}
        self.load = self.enterContext(patch.object(nc, "_load_operational_region", side_effect=self.load_region))
        self.enterContext(patch.object(nc, "_get_rasprod_current_period", side_effect=lambda d: d.selected))
        self.enterContext(patch.object(nc, "_operational_selected_region", side_effect=lambda d: browser.REGIONS[d.region]))
        self.picker = self.enterContext(patch.object(nc, "_open_date_picker", return_value=True))
        self.year = self.enterContext(patch.object(nc, "_navigate_calendar_to_year", side_effect=self.select_year))
        self.months = self.enterContext(patch.object(nc, "_get_available_month_indices", return_value=list(range(12))))
        self.month = self.enterContext(patch.object(nc, "_click_month_by_index", side_effect=self.select_month))
        self.mode = self.enterContext(patch.object(nc, "_click_area_mode", side_effect=self.select_mode))
        self.read = self.enterContext(patch.object(nc, "_read_construction_snapshot", side_effect=self.snapshot))
        self.sales = self.enterContext(patch.object(nc, "_parse_apartment_sales_html", side_effect=lambda html, region: self.snapshot(self.driver)))
        self.enterContext(patch("selenium.webdriver.support.ui.WebDriverWait", PollingWait))
        self.enterContext(patch.object(browser.time, "sleep"))

    def execute_script(self, script, *args):
        if script == browser.TRACK_REQUESTS:
            self.network = {"pending": 0, "idle": 500, "finished": []}
            return None
        if "const t=window.__operationalHistoryRequests" in script:
            return self.network_states.pop(0) if self.network_states else self.network
        if "captcha:" in script:
            return self.access
        if script == "return document.body.innerText":
            return "11 \u043e\u043a\u0442\u044f\u0431\u0440\u044f 2026 \u0433\u043e\u0434\u0430"
        if script == "document.body.click()":
            return None
        raise AssertionError("unexpected browser script")

    def load_region(self, driver, url, block, label):
        driver.block = block
        driver.region = next(region for region, name in browser.REGIONS.items() if name == label)
        driver.selected = self.bounds[block]
        driver.page_source = "rendered operational page"
        return driver, True

    def select_year(self, driver, year):
        driver.calendar_year = year
        return True

    def select_month(self, driver, index):
        driver.selected = f"{driver.calendar_year:04d}-{index + 1:02d}"
        self.network = {"pending": 0, "idle": 500,
                        "finished": [completed_request(driver.block, driver.selected, driver.region)]}
        return True

    def select_mode(self, driver, label):
        driver.kind = "living" if label == "\u0416\u0438\u043b\u0430\u044f" else "total"
        return True

    def snapshot(self, driver):
        return page_snapshot(driver.block, driver.selected, driver.region,
                             driver.kind if driver.block == "construction" else None)

    def test_client_uses_existing_controls_and_waits_without_issuing_api_calls(self):
        with browser.BrowserClient(self.root, delay=0) as client:
            self.assertEqual(client.bounds, self.bounds)
            construction = client.collect("construction", "2020-01")
            sales = client.collect("sales", "2020-01")
        self.chrome.assert_called_once_with(download_dir=self.root, headless=nc.HEADLESS)
        self.assertEqual((self.picker.call_count, self.year.call_count, self.month.call_count), (4, 4, 4))
        self.year.assert_has_calls([call(self.driver, 2020)] * 4)
        self.month.assert_has_calls([call(self.driver, 0)] * 4)
        self.assertEqual(self.mode.call_count, 4)
        for item in (construction, sales):
            history.validate_artifact(item)
            self.assertNotIn("responses", item)
        self.driver.execute_async_script.assert_not_called()
        self.http.assert_not_called()
        self.api_client.assert_not_called()
        self.driver.quit.assert_called_once()

    def test_history_defaults_to_browser_and_never_publishes_current(self):
        with patch.object(history, "publish_current", side_effect=AssertionError("current publication forbidden")) as publish:
            result = history.download_history(self.root, delay=0, progress=lambda _: None)
        self.assertTrue(result["complete"])
        self.assertEqual((result["attempted"], result["collected"]), (5, 5))
        self.assertEqual(result["current_file"], "")
        self.assertIsNone(result["current_publication_error"])
        self.assertFalse(list(self.root.glob("construction_operational_*.json")))
        self.assertEqual(result["new_files"], [self.root / history.BUNDLE_NAME])
        payload = json.loads(Path(result["history_file"]).read_text(encoding="utf-8"))
        self.assertTrue(all(row["evidence_kind"] == "browser_page"
                            for block in ("construction", "sales") for row in payload[block]))
        self.assertEqual(len(payload["construction"]), 12)
        self.assertEqual(len(payload["sales"]), 4)
        publish.assert_not_called()
        self.http.assert_not_called()
        self.api_client.assert_not_called()
        self.driver.execute_async_script.assert_not_called()

    def test_client_records_initial_date_without_assigning_global_date_to_history(self):
        def dated_snapshot(driver):
            raw = self.snapshot(driver)
            raw["report_date"] = "31.01.2020" if driver.block == "construction" else "11.10.2026"
            return raw

        self.read.side_effect = dated_snapshot
        self.sales.side_effect = lambda html, region: dated_snapshot(self.driver)
        with browser.BrowserClient(self.root, delay=0) as client:
            construction = client.collect("construction", "2020-01")
            sales = client.collect("sales", "2020-01")
        self.assertEqual({row["report_date"] for row in construction["rows"]}, {"31.01.2020"})
        self.assertEqual({row["report_date"] for row in sales["rows"]}, {""})
        self.assertEqual({item["snapshot"]["report_date"] for item in sales["observations"]}, {"11.10.2026"})
        for item in (construction, sales):
            self.assertEqual({proof["selection"]["initial_report_date"] for proof in item["observations"]}, {"11.10.2026"})
            history.validate_artifact(item)

    def test_period_control_waits_for_matching_request_idle_and_requested_label(self):
        with browser.BrowserClient(self.root, delay=0) as client:
            client._load("construction", "msk")
            done = completed_request("construction", "2020-01", "msk")
            self.network_states = [
                {"pending": 1, "idle": 500, "finished": [done]},
                {"pending": 0, "idle": 100, "finished": [done]},
                {"pending": 0, "idle": 500, "finished": [completed_request("construction", "2020-01", "rf")]},
                {"pending": 0, "idle": 500, "finished": [done]},
            ]
            proof = client._select("construction", "2020-01", "msk")
            self.assertEqual(proof["initial_report_date"], "11.10.2026")
            self.assertEqual(proof["completed_requests"], [done])
            self.assertEqual(self.network_states, [])

    def test_month_label_alone_or_pending_requests_cannot_complete_selection(self):
        for state in ({"pending": 0, "idle": 500, "finished": []},
                      {"pending": 1, "idle": 500, "finished": [completed_request("sales", "2020-01", "rf")]}):
            with self.subTest(state=state), browser.BrowserClient(self.root, delay=0) as client:
                client._load("sales", "rf")
                self.month.side_effect = lambda driver, index: setattr(driver, "selected", "2020-01") or True
                self.network = state
                with self.assertRaises(TimeoutException):
                    client._select("sales", "2020-01", "rf")

    def test_completed_request_cannot_prove_a_stale_selected_label(self):
        with browser.BrowserClient(self.root, delay=0) as client:
            client._load("sales", "rf")
            self.month.side_effect = lambda driver, index: True
            self.network = {"pending": 0, "idle": 500,
                            "finished": [completed_request("sales", "2020-01", "rf")]}
            with self.assertRaises(TimeoutException):
                client._select("sales", "2020-01", "rf")

    def test_snapshot_requires_repeated_stable_values_and_idle_network(self):
        with browser.BrowserClient(self.root, delay=0) as client:
            client._load("construction", "rf")
            self.select_mode(self.driver, "\u0416\u0438\u043b\u0430\u044f")
            self.select_year(self.driver, 2020)
            self.select_month(self.driver, 0)
            first = page_snapshot("construction", "2020-01", "rf", "living")
            second = {**first, "area_thousand_m2": "104000 " + AREA_UNIT}
            self.read.side_effect = [first, second, second, second]
            self.assertEqual(client._snapshot("construction", "rf", "living", "2020-01"), second)
            self.assertEqual(self.read.call_count, 4)

    def test_stale_values_wrong_region_mode_or_unstable_render_are_not_snapshots(self):
        for failure in ("period", "region", "mode", "unstable", "pending"):
            with self.subTest(failure=failure), browser.BrowserClient(self.root, delay=0) as client:
                client._load("construction", "rf")
                self.select_mode(self.driver, "\u0416\u0438\u043b\u0430\u044f")
                self.select_year(self.driver, 2020)
                self.select_month(self.driver, 0)
                raw = page_snapshot("construction", "2020-01", "rf", "living")
                if failure == "period":
                    raw["report_period"] = "2020-03"
                elif failure == "mode":
                    raw["area_kind"] = "total"
                elif failure == "region":
                    self.driver.region = "msk"
                elif failure == "pending":
                    self.network["pending"] = 1
                if failure == "unstable":
                    reads = iter(range(20))
                    self.read.side_effect = lambda driver: {**raw, "area_thousand_m2": str(100000 + next(reads))}
                else:
                    self.read.side_effect = lambda driver: deepcopy(raw)
                with self.assertRaises(TimeoutException):
                    client._snapshot("construction", "rf", "living", "2020-01")

    def test_disabled_month_and_failed_picker_do_not_fall_back_to_latest(self):
        with browser.BrowserClient(self.root, delay=0) as client:
            client._load("sales", "rf")
            self.months.return_value = [1, 2]
            with self.assertRaises(api.EmptyReport):
                client._select("sales", "2020-01", "rf")
            self.month.assert_not_called()
            self.picker.return_value = False
            with self.assertRaises(api.ReportError):
                client._select("sales", "2020-01", "rf")

    def test_access_verification_stops_without_picker_or_manual_requests(self):
        self.access = {"denied": False, "captcha": True}
        with self.assertRaises(api.AccessVerificationRequired):
            with browser.BrowserClient(self.root, delay=0):
                self.fail("verification must stop initialization")
        self.load.assert_not_called()
        self.picker.assert_not_called()
        self.driver.execute_async_script.assert_not_called()
        self.driver.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
