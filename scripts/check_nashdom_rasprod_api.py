"""Regression checks for exact, resumable and bounded rasprod API collection."""
from __future__ import annotations

import copy
import json
import sys
import tempfile
import unittest
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
import nashdom_checker as nc
from pipeline.downloaders import nashdom_rasprod_api as api
from pipeline.parsers import nashdom_rasprodannost as parser


def index(month=8):
    return {"squareSumIndex": 15000000 + month * 100,
            "soldPercIndex": 40 + month / 100, "soldReadyPercIndex": 80 + month / 100,
            "readyPercIndex": 50 + month / 100}


def series(months=(8, 7)):
    return {"dynamicCharts": [
        {"dynamicChartType": chart, "repYears": [{"repYear": 2026,
            "repMonths": [{"repMonth": month, "value": index(month)[key]} for month in months]}]}
        for chart, key, _, _ in api.METRICS
    ]}


def payload(month=8):
    values = index(month)
    row = {"key": "Fixture", "regionCd": 77, "readyYear": 2026,
           "squareSum": values["squareSumIndex"], "soldPerc": values["soldPercIndex"],
           "readyPerc": values["readyPercIndex"], "soldReadyPerc": values["soldReadyPercIndex"]}
    charts = {"charts": [{"chartType": key, "data": [copy.deepcopy(row)]} for key in api.SECTIONS]}
    return values, charts, {"readyYears": [row]}


class Driver:
    page_source = "<table>"
    def get(self, url): pass
    def set_page_load_timeout(self, seconds): pass
    def quit(self): pass


class Checks(unittest.TestCase):
    def make_report(self, region="msk", month=8):
        return api.report(region, 2026, month, index(month), *payload(month), page_url="https://example.test")

    def test_api_report_preserves_units_and_parser_contract(self):
        report = self.make_report()
        self.assertEqual(float(report["kpi"][0]["значение"]), index()["squareSumIndex"] / 1000)
        self.assertEqual(report["kpi"][1]["значение"], "40.08")
        self.assertEqual(report["tables"]["Девелоперы"][0]["Объем жил. строительства"], "15000800.0")
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "rasprodannost_20261009.json"
            path.write_text(json.dumps([report]), encoding="utf-8")
            frame = parser.parse(path)
        self.assertFalse(frame.empty)
        total = frame[(frame["entity_type"] == "kpi_total") & (frame["view"] == "construction_volume_thousand_m2")]
        self.assertEqual(total.iloc[0]["value"], 15000.8)
        self.assertEqual(set(frame["region"]), {"Москва"})

    def test_mismatched_period_index_rejected(self):
        with self.assertRaisesRegex(api.ReportError, "index/history mismatch"):
            api.report("rf", 2026, 8, index(7), *payload(8), page_url="https://example.test")

    def test_missing_developer_metric_is_preserved_as_blank(self):
        values, charts, ready = payload()
        charts["charts"][2]["data"][0]["soldPerc"] = None
        result = api.report("msk", 2026, 8, index(), values, charts, ready, page_url="https://example.test")
        self.assertEqual(result["tables"]["Девелоперы"][0]["Распроданность"], "")

    def test_changed_history_cannot_reuse_cached_period(self):
        report = self.make_report()
        self.assertTrue(api.matches_history(report, index()))
        self.assertFalse(api.matches_history(report, index(7)))

    def test_wrong_moscow_scope_and_table_total_rejected(self):
        values, charts, ready = payload()
        charts["charts"][1]["data"][0]["regionCd"] = 50
        with self.assertRaisesRegex(api.ReportError, "different region"):
            api.report("msk", 2026, 8, index(), values, charts, ready, page_url="https://example.test")
        charts["charts"][1]["data"][0]["squareSum"] += 100
        with self.assertRaisesRegex(api.ReportError, "table total"):
            api.report("rf", 2026, 8, index(), values, charts, ready, page_url="https://example.test")

    def test_missing_duplicate_and_non_numeric_history_rejected(self):
        original = series()
        self.assertEqual(set(api.history(original)), {(2026, 8), (2026, 7)})
        cases = []
        missing = copy.deepcopy(original)
        missing["dynamicCharts"][0]["repYears"][0]["repMonths"].pop()
        cases.append(missing)
        duplicate = copy.deepcopy(original)
        duplicate["dynamicCharts"].append(copy.deepcopy(duplicate["dynamicCharts"][0]))
        cases.append(duplicate)
        invalid = copy.deepcopy(original)
        invalid["dynamicCharts"][0]["repYears"][0]["repMonths"][0]["value"] = None
        cases.append(invalid)
        for case in cases:
            with self.assertRaises(api.ReportError): api.history(case)

    def test_explicit_request_scope_and_missing_response_rejected(self):
        query = parse_qs(urlsplit(api.urls("msk", ["index"], 2026, 8)[0]).query)
        self.assertEqual(query, {"repYear": ["2026"], "repMonth": ["8"], "regionCd": ["77"]})
        class Fake:
            def set_script_timeout(self, seconds): pass
            def execute_async_script(self, *args):
                return [{"url": args[1][0], "status": 503}]
        with self.assertRaisesRegex(api.ReportError, "request failed"):
            api.fetch(Fake(), api.urls("rf", ["dynamics"]))

    def collect(self, directory, callback, factory=Driver):
        with ExitStack() as stack:
            stack.enter_context(patch.object(nc, "DOWNLOAD_DIR", directory))
            stack.enter_context(patch.object(nc, "create_chrome", side_effect=factory))
            stack.enter_context(patch.object(nc, "selenium_sleep"))
            stack.enter_context(patch.object(api, "fetch", side_effect=callback))
            stack.enter_context(redirect_stdout(StringIO()))
            state = {}
            result = nc.fetch_rasprodannost(state)
        return result, state

    def test_complete_collection_and_incremental_resume(self):
        calls = []
        def callback(driver, request_urls):
            calls.append(request_urls)
            if "/dynamics?" in request_urls[0]: return [series()]
            month = int(parse_qs(urlsplit(request_urls[0]).query)["repMonth"][0])
            return list(payload(month))
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            (files, ok), state = self.collect(folder, callback)
            self.assertTrue(ok)
            self.assertEqual(len(files), 2)
            self.assertTrue(state["rasprodannost"]["complete"])
            self.assertEqual(len(json.loads(next(p for p in files if p.suffix == ".json").read_text())), 4)
            self.assertFalse((folder / "._rasprodannost_progress.json").exists())
            calls.clear()
            (files, ok), _ = self.collect(folder, callback)
            self.assertTrue(ok)
            self.assertEqual(len(calls), 4)  # history + latest report per region
            self.assertTrue(all("repMonth=8" in batch[0] for batch in calls if "/dynamics?" not in batch[0]))

    def test_three_failures_stop_region_and_preserve_published_file(self):
        calls = []
        def callback(driver, request_urls):
            if "/dynamics?" in request_urls[0]: return [series((8, 7, 6, 5, 4))]
            calls.append(request_urls)
            raise api.ReportError("fixture network failure")
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp)
            published = folder / "rasprodannost_20260101.json"
            # Legacy data cannot make failed API migration look complete.
            legacy = [self.make_report("rf"), self.make_report("msk")]
            for row in legacy: row.pop("api_verified")
            original = json.dumps(legacy).encode()
            published.write_bytes(original)
            (files, ok), _ = self.collect(folder, callback)
            self.assertFalse(ok)
            self.assertEqual(files, [])
            self.assertEqual(len(calls), 6)
            self.assertEqual(published.read_bytes(), original)
            self.assertTrue((folder / "._rasprodannost_progress.json").exists())

    def test_new_browser_every_twenty_reports_keeps_progress(self):
        drivers = []
        class CountingDriver(Driver):
            def __init__(self):
                self.reports = 0
                self.closed = False
                drivers.append(self)
            def quit(self): self.closed = True
        def callback(driver, request_urls):
            if "/dynamics?" in request_urls[0]:
                data = series(tuple(range(1, 13)))
                for chart in data["dynamicCharts"]:
                    chart["repYears"] = [
                        {**copy.deepcopy(chart["repYears"][0]), "repYear": year} for year in (2024, 2025)
                    ]
                return [data]
            driver.reports += 1
            self.assertLessEqual(driver.reports, 20)
            month = int(parse_qs(urlsplit(request_urls[0]).query)["repMonth"][0])
            return list(payload(month))
        with tempfile.TemporaryDirectory() as tmp:
            (files, ok), _ = self.collect(Path(tmp), callback, CountingDriver)
            self.assertTrue(ok)
            data = json.loads(next(p for p in files if p.suffix == ".json").read_text())
            self.assertEqual(len(data), 48)
        self.assertEqual(len(drivers), 4)
        self.assertTrue(all(driver.closed for driver in drivers))


if __name__ == "__main__":
    unittest.main()
