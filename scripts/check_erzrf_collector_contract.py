"""Offline ERZ collector contracts: real XLSX fixtures, no browser or notifications."""
from __future__ import annotations

import copy
import hashlib
import json
import os
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlparse

import pandas as pd

os.environ["TDM_DISABLED"] = "1"
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
import erzrf_checker as ec  # noqa: E402


# These schema signatures come from the existing 20260702 raw files, whose
# historical names were incorrect. Fixtures use invented developer values.
def fixture_frame(kind: str) -> pd.DataFrame:
    row = {"Место": 1, "Наименование, регион": "Fixture developer, регион"}
    if kind == "obyem_stroitelstva":
        row["Строится, м²"] = 125
    elif kind in {"obyem_vvoda", "nakopl_vvod"}:
        row["Введено, м²"] = 250
        if kind == "nakopl_vvod":
            row["Ушел с рынка"] = "Нет"
    elif kind == "potreb_kachestva":
        row.update({"Строится, м²": 500, "Средняя оценка": 79.84,
                    "ЖК/ПТ, всего в расчете": 15})
    elif kind == "skorost":
        row.update({"Скорость строительства, дней/дом": 266.58,
                    "Введено МКД по ДДУ за 3 года": 100201,
                    "Средняя площадь дома, м²": 8350,
                    "Средняя оценка ЖК по м²": None})
    return pd.DataFrame([row])


def selection(request: ec.TopExport) -> dict:
    return {"url": ec._build_top_url(request.region, request.sorting_key), "developer_totals": [1], "selects": [
        {"id": "sorting", "value": str(ec.TOP_TYPES[request.sorting_key]),
         "selected_text": next(item["label"] for item in ec.SORTINGS if item["key"] == request.sorting_key),
         "options": [{"value": str(ec.TOP_TYPES[item["key"]]), "text": item["label"]} for item in ec.SORTINGS]},
        {"id": "region", "value": "0" if request.region_key == "rf" else "143443001",
         "selected_text": "РФ" if request.region_key == "rf" else "г.Москва",
         "options": [{"value": "0", "text": "РФ"}, {"value": "143443001", "text": "г.Москва"}]},
        {"id": "year", "value": str(request.year or 2026), "selected_text": str(request.year or 2026),
         "options": [{"value": str(year), "text": str(year)} for year in range(2022, 2027)]},
    ]}


class FakeDriver:
    def __init__(self):
        self.request = ec.TopExport("rf", "obyem_stroitelstva")
        self.closed = False
        self.destinations = []

    def get(self, url):
        parsed = urlparse(url)
        kind = next(key for key, value in ec.TOP_TYPES.items()
                    if str(value) == parse_qs(parsed.query)["topType"][0])
        self.request = ec.TopExport("msk" if "moskva" in parsed.path else "rf", kind)

    def set_page_load_timeout(self, timeout):
        pass

    def execute_cdp_cmd(self, command, parameters):
        assert command == "Browser.setDownloadBehavior"
        self.destinations.append(Path(parameters["downloadPath"]))

    def quit(self):
        self.closed = True


class CollectorContract(unittest.TestCase):
    def setUp(self):
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        self.base = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(patch.object(ec, "DOWNLOAD_DIR", self.base / "raw" / "erzrf"))
        self.stack.enter_context(patch.object(ec, "STATE_FILE", self.base / "state.json"))
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.request = ec.TopExport("rf", "obyem_stroitelstva")

    def workbook(self, kind, name="download.xlsx", frame=None):
        path = self.base / name
        path.parent.mkdir(parents=True, exist_ok=True)
        (fixture_frame(kind) if frame is None else frame).to_excel(path, index=False)
        return path

    def browser_stubs(self, driver):
        self.stack.enter_context(patch.object(ec, "create_chrome", return_value=driver))
        self.stack.enter_context(patch.object(ec, "_ensure_logged_in", return_value=True))
        self.stack.enter_context(patch.object(ec, "_wait_for_top_content", return_value=True))
        self.stack.enter_context(patch.object(ec, "_read_top_selection", side_effect=lambda d: selection(d.request)))
        self.stack.enter_context(patch.object(ec, "_click_download_excel", return_value={"clicked": True}))
        self.stack.enter_context(patch.object(ec, "_scrape_developers_from_table", return_value=[
            {"name": "Fixture developer", "card_url": "https://erzrf.ru/zastroyschiki/fixture",
             "cells": ["1", "Fixture developer", "125"]},
        ]))

    def download(self, driver=None):
        staging = self.base / "staging"
        staging.mkdir(exist_ok=True)
        return ec._download_top_export(driver or FakeDriver(), self.request, "20260928", staging, ec.TOP_TYPES)

    def test_unknown_sorting_and_region_never_fall_back(self):
        with self.assertRaises(ValueError):
            ec._build_top_url(ec.REGIONS[0], "typo")
        with self.assertRaises(ValueError):
            ec._build_top_url({**ec.REGIONS[0], "path": "moskva"}, "obyem_stroitelstva")
        with self.assertRaises(ValueError):
            ec._build_top_url(ec.REGIONS[0], "nakopl_vvod", {"obyem_stroitelstva": 0})

    def test_verified_mapping_and_dynamic_options(self):
        self.assertEqual(ec.TOP_TYPES, {"obyem_stroitelstva": 0, "obyem_vvoda": 1,
                                      "nakopl_vvod": 4, "potreb_kachestva": 2, "skorost": 3})
        self.assertEqual(ec._sorting_control(selection(self.request))[1], ec.TOP_TYPES)

    def test_mapping_missing_ambiguous_or_changed_fails(self):
        original = selection(self.request)
        cases = []
        missing = copy.deepcopy(original)
        missing["selects"][0]["options"].pop()
        cases.append(missing)
        duplicate = copy.deepcopy(original)
        duplicate["selects"].append(copy.deepcopy(duplicate["selects"][0]))
        cases.append(duplicate)
        changed = copy.deepcopy(original)
        changed["selects"][0]["options"][0]["value"] = "99"
        cases.append(changed)
        for case in cases:
            with self.subTest(case=case), self.assertRaises(ec.TopExportError):
                ec._sorting_control(case)

    def test_request_requires_matching_selected_sort_region_url_and_year(self):
        request = ec.TopExport("msk", "obyem_vvoda", 2024)
        good = selection(request)
        with patch.object(ec, "_read_top_selection", return_value=good):
            self.assertEqual(ec._top_request_evidence(None, request, ec.TOP_TYPES)["selected_year"], 2024)
        cases = []
        for index, value in [(0, "0"), (1, "0"), (2, "2025")]:
            bad = copy.deepcopy(good)
            bad["selects"][index]["value"] = value
            cases.append(bad)
        for suffix in ["&topType=0", "&regionKey=0"]:
            bad = copy.deepcopy(good)
            bad["url"] += suffix
            cases.append(bad)
        bad = copy.deepcopy(good)
        bad["url"] = bad["url"].replace("moskva?", "rf?")
        cases.append(bad)
        for case in cases:
            with self.subTest(case=case), patch.object(ec, "_read_top_selection", return_value=case):
                with self.assertRaises(ec.TopExportError):
                    ec._top_request_evidence(None, request, ec.TOP_TYPES)

    def test_all_five_real_workbook_signatures_pass(self):
        for kind in ec.TOP_TYPES:
            with self.subTest(kind=kind):
                report = ec._excel_contract(self.workbook(kind), kind)
                self.assertEqual(report["rows"], 1)

    def test_historical_three_mislabelled_schemas_fail(self):
        cases = [("potreb_kachestva", "nakopl_vvod"), ("skorost", "potreb_kachestva"),
                 ("nakopl_vvod", "skorost"), ("potreb_kachestva", "obyem_stroitelstva"),
                 ("obyem_vvoda", "nakopl_vvod"), ("nakopl_vvod", "obyem_vvoda")]
        for actual, requested in cases:
            with self.subTest(actual=actual, requested=requested), self.assertRaises(ValueError):
                ec._excel_contract(self.workbook(actual), requested)

    def test_fake_zip_and_html_are_invalid_xlsx(self):
        path = self.base / "download.xlsx"
        for content in [b"PK\x03\x04not-a-workbook", b"<html>login</html>"]:
            path.write_bytes(content)
            self.assertFalse(ec._validate_downloaded_excel(path, "obyem_stroitelstva"))

    def test_empty_missing_invalid_and_nonfinite_metric_fail(self):
        good = fixture_frame("obyem_stroitelstva")
        cases = [good.iloc[:0], good.drop(columns=["Строится, м²"])]
        for value in [None, -1, "NaN", "inf", True, "no data"]:
            bad = good.copy()
            bad["Строится, м²"] = value
            cases.append(bad)
        for case in cases:
            with self.subTest(frame=case.to_dict()), self.assertRaises(ValueError):
                ec._excel_contract(self.workbook("obyem_stroitelstva", frame=case), "obyem_stroitelstva")

    def test_invalid_names_and_ranks_fail(self):
        for column, value in [("Наименование, регион", None), ("Место", 0), ("Место", 1.5)]:
            bad = fixture_frame("obyem_stroitelstva")
            bad[column] = value
            with self.subTest(column=column, value=value), self.assertRaises(ValueError):
                ec._excel_contract(self.workbook("obyem_stroitelstva", frame=bad), "obyem_stroitelstva")

    def test_success_publishes_semantic_xlsx_and_provenance(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("obyem_stroitelstva").to_excel(path, index=False)
            return path
        with patch.object(ec, "wait_for_download", side_effect=wait):
            target = self.download(driver)
        evidence = json.loads(target.with_suffix(".xlsx.provenance.json").read_text(encoding="utf-8"))
        self.assertEqual(evidence["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertFalse(evidence["period_verified"])
        self.assertEqual(evidence["workbook"]["rows"], 1)

    def test_invalid_schema_does_not_replace_previous_file(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        target = ec.DOWNLOAD_DIR / self.request.filename("20260928")
        target.parent.mkdir(parents=True)
        target.write_bytes(b"previous-version-preserved")
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("potreb_kachestva").to_excel(path, index=False)
            return path
        with patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ValueError):
            self.download(driver)
        self.assertEqual(target.read_bytes(), b"previous-version-preserved")
        self.assertFalse(target.with_suffix(".xlsx.provenance.json").exists())

    def test_incomplete_full_list_workbook_is_not_published(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        expected = selection(driver.request)
        expected["developer_totals"] = [100]
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("obyem_stroitelstva").to_excel(path, index=False)
            return path
        with patch.object(ec, "_read_top_selection", return_value=expected), \
                patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ec.TopExportError):
            self.download(driver)
        self.assertFalse(ec.DOWNLOAD_DIR.exists())

    def test_late_file_outside_current_attempt_is_rejected(self):
        self.browser_stubs(FakeDriver())
        late = self.workbook("obyem_stroitelstva", "earlier_attempt/site.xlsx")
        with patch.object(ec, "wait_for_download", return_value=late), self.assertRaises(ec.TopExportError):
            self.download()
        self.assertTrue(late.exists())
        self.assertFalse(ec.DOWNLOAD_DIR.exists())

    def test_multiple_candidates_are_rejected(self):
        self.browser_stubs(FakeDriver())
        def wait(folder, **kwargs):
            for name in ("one.xlsx", "two.xlsx"):
                fixture_frame("obyem_stroitelstva").to_excel(folder / name, index=False)
            return folder / "two.xlsx"
        with patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ec.TopExportError):
            self.download()

    def test_postclick_selection_change_is_rejected(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame("obyem_stroitelstva").to_excel(path, index=False)
            driver.request = ec.TopExport("msk", "obyem_stroitelstva")
            return path
        with patch.object(ec, "wait_for_download", side_effect=wait), self.assertRaises(ec.TopExportError):
            self.download(driver)

    def test_timeout_closes_session_without_next_export_and_keeps_success_state(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        state = {"erzrf_top": {"last_run": "previous-success"}}
        with patch.object(ec, "wait_for_download", return_value=None) as waiter:
            result = ec.fetch_top(state)
        self.assertFalse(result.complete)
        self.assertEqual(waiter.call_count, 1)
        self.assertEqual(len(driver.destinations), 1)
        self.assertTrue(driver.closed)
        self.assertFalse(driver.destinations[0].exists())
        self.assertEqual(state["erzrf_top"]["last_run"], "previous-success")
        self.assertFalse(state["erzrf_top_attempt"]["complete"])
        self.assertEqual(len(state["erzrf_top_attempt"]["missing_files"]), 22)

    def test_full_plan_has_twenty_exports_and_ten_explicit_years(self):
        plan = ec._top_export_plan()
        self.assertEqual(len(plan), 20)
        self.assertEqual(sum(request.year is not None for request in plan), 10)
        self.assertEqual(len({request.filename("20260928") for request in plan}), 20)

    def test_year_switch_waits_for_changed_table_not_just_assigned_value(self):
        driver = FakeDriver()
        driver.request = ec.TopExport("rf", "obyem_vvoda")
        initial = selection(driver.request)
        selected = selection(ec.TopExport("rf", "obyem_vvoda", 2024))
        driver.execute_script = lambda *args: True

        class ImmediateWait:
            def __init__(self, browser, timeout):
                self.browser = browser
            def until(self, predicate):
                if not predicate(self.browser):
                    raise ec.TimeoutException("fixture: no response table change")

        for changed in (False, True):
            with self.subTest(changed=changed), patch.object(ec, "WebDriverWait", ImmediateWait), \
                    patch.object(ec, "_read_top_selection", side_effect=[initial, selected]), \
                    patch.object(ec, "_top_table_digest", side_effect=["before", "after" if changed else "before"]):
                self.assertEqual(ec._switch_year_filter(driver, 2024), changed)
                self.assertEqual(bool(driver._erzrf_year_evidence), changed)

    def test_no_authorization_preserves_previous_success_and_closes_browser(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        state = {"erzrf_top": {"last_run": "previous"}}
        with patch.object(ec, "_ensure_logged_in", return_value=False), patch.object(ec, "wait_for_download") as waiter:
            result = ec.fetch_top(state)
        self.assertFalse(result.complete)
        self.assertEqual(state["erzrf_top"]["last_run"], "previous")
        self.assertEqual(waiter.call_count, 0)
        self.assertTrue(driver.closed)

    def test_partial_export_failure_remains_retryable_and_keeps_previous_success(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        calls = []
        def wait(folder, **kwargs):
            calls.append(folder)
            if len(calls) > 1:
                return None
            path = folder / "site.xlsx"
            fixture_frame(driver.request.sorting_key).to_excel(path, index=False)
            return path
        state = {"erzrf_top": {"last_run": "previous"}}
        with patch.object(ec, "wait_for_download", side_effect=wait):
            result = ec.fetch_top(state)
        self.assertEqual(len(result.files), 1)
        self.assertFalse(result.complete)
        self.assertEqual(len(result.missing_files), 21)
        self.assertEqual(len(calls), 2)
        self.assertNotEqual(calls[0], calls[1])
        self.assertEqual(state["erzrf_top"]["last_run"], "previous")
        self.assertTrue(driver.closed)

    def test_complete_fixture_collection_advances_success_state(self):
        driver = FakeDriver()
        self.browser_stubs(driver)
        def wait(folder, **kwargs):
            path = folder / "site.xlsx"
            fixture_frame(driver.request.sorting_key).to_excel(path, index=False)
            return path
        def switch(browser, year):
            browser.request = ec.TopExport(browser.request.region_key, browser.request.sorting_key, year)
            browser._erzrf_year_evidence = {"year": year, "table_changed": True}
            return True
        developers = [{"name": f"Fixture {i}", "card_url": f"https://erzrf.ru/zastroyschiki/fixture-{i}"}
                      for i in range(100)]
        state = {}
        with patch.object(ec, "wait_for_download", side_effect=wait), \
                patch.object(ec, "_switch_year_filter", side_effect=switch), \
                patch.object(ec, "_collect_top_n_developers", return_value=developers):
            result = ec.fetch_top(state)
        self.assertTrue(result.complete)
        self.assertEqual(len(result.files), 22)
        self.assertTrue(state["erzrf_top_attempt"]["complete"])
        self.assertEqual(len(state["erzrf_top"]["files"]), 22)
        self.assertEqual(len(set(driver.destinations)), 20)
        self.assertTrue(driver.closed)

    def test_run_rejects_partial_json_only_empty_and_error_results(self):
        required = {"one.xlsx", "two.xlsx", "developers.json"}
        cases = [ec.TopFetchResult(required),
                 ec.TopFetchResult(required, [Path("developers.json")]),
                 ec.TopFetchResult(required, [Path("one.xlsx"), Path("developers.json")]),
                 ec.TopFetchResult(required, [Path(name) for name in required], "failed")]
        for result in cases:
            with self.subTest(result=result), patch.object(ec, "fetch_top", return_value=result):
                files, ok = ec.run(["top"])
                self.assertFalse(ok)
                self.assertEqual(files, result.files)
        with patch.object(ec, "fetch_top", return_value=ec.TopFetchResult(required, [Path(name) for name in required])):
            self.assertTrue(ec.run(["top"])[1])


if __name__ == "__main__":
    unittest.main()
