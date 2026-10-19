"""Offline regressions for the current-page collector, independent of history."""
from contextlib import redirect_stdout
from copy import deepcopy
from datetime import datetime
import hashlib
from io import StringIO
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, call, patch
from urllib.parse import unquote, urlsplit

from selenium.common.exceptions import WebDriverException

import nashdom_checker as nc
from pipeline.download_provenance import receipt_path, record_download


RF = "\u0412\u0441\u0435"
MOSCOW = "\u0413\u043e\u0440\u043e\u0434 \u041c\u043e\u0441\u043a\u0432\u0430"
LIVING = "\u0416\u0438\u043b\u0430\u044f"
TOTAL = "\u041e\u0431\u0449\u0430\u044f"
MONTHS = (
    "\u042f\u043d\u0432\u0430\u0440\u044c", "\u0424\u0435\u0432\u0440\u0430\u043b\u044c",
    "\u041c\u0430\u0440\u0442", "\u0410\u043f\u0440\u0435\u043b\u044c", "\u041c\u0430\u0439",
    "\u0418\u044e\u043d\u044c", "\u0418\u044e\u043b\u044c", "\u0410\u0432\u0433\u0443\u0441\u0442",
    "\u0421\u0435\u043d\u0442\u044f\u0431\u0440\u044c", "\u041e\u043a\u0442\u044f\u0431\u0440\u044c",
    "\u041d\u043e\u044f\u0431\u0440\u044c", "\u0414\u0435\u043a\u0430\u0431\u0440\u044c",
)
SOURCE_DATES = {
    "19.10.2026": "19 \u043e\u043a\u0442\u044f\u0431\u0440\u044f 2026 \u0433\u043e\u0434\u0430",
    "30.09.2026": "30 \u0441\u0435\u043d\u0442\u044f\u0431\u0440\u044f 2026 \u0433\u043e\u0434\u0430",
    "": "",
}


class FixedDateTime(datetime):
    @classmethod
    def now(cls, tz=None):
        return cls(2026, 10, 19, 13, 14, 15, tzinfo=tz)


def period(year, month):
    return f"{MONTHS[month - 1]} {year}"


def payload():
    result = {"source": "construction_operational", "scraped_at": "2026-10-01T12:00:00",
              "construction": [], "sales": []}
    for region, label, living, total, scale in (("rf", RF, 121222, 204448, 10),
                                               ("msk", MOSCOW, 15692, 27142, 1)):
        for kind, value in (("living", living), ("total", total)):
            result["construction"].append({
                "region_key": region, "region": label, "area_kind": kind,
                "report_period": period(2026, 10), "report_date": "19.10.2026",
                "area_thousand_m2": str(value),
            })
        result["sales"].append({
            "region_key": region, "region": label, "report_period": period(2026, 9),
            "report_date": "30.09.2026", "metrics": {
                "total_living_thousand_m2": str(100 * scale),
                "sales_open_thousand_m2": str(80 * scale),
                "sold_thousand_m2": str(30 * scale), "sold_pct": "30%",
                "unsold_thousand_m2": str(50 * scale), "unsold_pct": "50%",
                "sales_not_open_thousand_m2": str(20 * scale), "sales_not_open_pct": "20%",
                "price_per_m2_rub": "220331", "funds_million_rub": "8334247",
            },
        })
    return result


def sales_html(row):
    parts = [f"<html><body><div>\u041e\u0442\u0447\u0435\u0442\u043d\u044b\u0439 "
             f"\u043f\u0435\u0440\u0438\u043e\u0434 {row['report_period']}. "
             f"{SOURCE_DATES[row['report_date']]}</div>"]
    for label, key, css, pct in (
        ("\u0412\u0441\u0435\u0433\u043e \u0436\u0438\u043b\u043e\u0439 \u043f\u043b\u043e\u0449\u0430\u0434\u0438",
         "total_living_thousand_m2", "IndicatorValue-ab", None),
        ("\u041f\u0440\u043e\u0434\u0430\u0436\u0438 \u043e\u0442\u043a\u0440\u044b\u0442\u044b",
         "sales_open_thousand_m2", "IndicatorValue-ab", None),
        ("\u041f\u0440\u043e\u0434\u0430\u043d\u043d\u0430\u044f \u043f\u043b\u043e\u0449\u0430\u0434\u044c",
         "sold_thousand_m2", "Value-sc-18spgfx-a", "sold_pct"),
        ("\u041d\u0435\u043f\u0440\u043e\u0434\u0430\u043d\u043d\u0430\u044f \u043f\u043b\u043e\u0449\u0430\u0434\u044c",
         "unsold_thousand_m2", "Value-sc-18spgfx-a", "unsold_pct"),
        ("\u041f\u0440\u043e\u0434\u0430\u0436\u0438 \u043d\u0435 \u043e\u0442\u043a\u0440\u044b\u0442\u044b",
         "sales_not_open_thousand_m2", "Value-sc-18spgfx-a", "sales_not_open_pct"),
        ("\u0421\u0442\u043e\u0438\u043c\u043e\u0441\u0442\u044c 1 \u043a\u0432\u0430\u0434\u0440\u0430\u0442\u043d\u043e\u0433\u043e \u043c\u0435\u0442\u0440\u0430",
         "price_per_m2_rub", "Value-sc-182uuls-a", None),
        ("\u041e\u0431\u044a\u0435\u043c \u043f\u0440\u0438\u0432\u043b\u0435\u0447\u0435\u043d\u043d\u044b\u0445 \u0441\u0440\u0435\u0434\u0441\u0442\u0432",
         "funds_million_rub", "Value-sc-182uuls-a", None),
    ):
        parts.append(f'<div><div>{label}</div><span class="{css}">{row["metrics"].get(key, "")}</span>')
        if pct:
            parts.append(f'<span class="Value-sc-1x9zma-a">{row["metrics"].get(pct, "")}</span>')
        parts.append("</div>")
    return "".join(parts) + "</body></html>"


class CurrentBrowserTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.root = Path(self.directory.name)
        self.raw = self.root / "raw"
        self.enterContext(patch.object(nc, "DOWNLOAD_DIR", self.raw))
        self.enterContext(patch.object(nc, "STATE_FILE", self.root / "state" / "nashdom.json"))
        self.enterContext(patch.object(nc, "datetime", FixedDateTime))
        self.driver = Mock(name="current_browser")
        self.factory = self.enterContext(patch.object(nc, "create_chrome", return_value=self.driver))
        self.load = self.enterContext(patch.object(nc, "_load_operational_region", side_effect=self.load_region))
        self.mode = self.enterContext(patch.object(nc, "_click_area_mode", side_effect=self.click_mode))
        self.read = self.enterContext(patch.object(nc, "_read_construction_snapshot", side_effect=self.read_snapshot))
        self.parse = self.enterContext(patch.object(nc, "_parse_apartment_sales_html", wraps=nc._parse_apartment_sales_html))
        self.http = self.enterContext(patch("requests.sessions.Session.request", side_effect=AssertionError("network forbidden")))
        self.current = payload()
        self.state = {"monitoring_2_0": {"filename": "unrelated.xlsx"}}
        self.failed_region = None
        self.failed_mode = None

    def load_region(self, driver, url, page, label):
        driver.label = label
        if page == "sales":
            row = next(row for row in self.current["sales"] if row["region"] == label)
            driver.page_source = sales_html(row)
        return driver, (page, label) != self.failed_region

    def click_mode(self, driver, label):
        driver.kind = "living" if label == LIVING else "total"
        return (driver.label, label) != self.failed_mode

    def read_snapshot(self, driver):
        index = (0 if driver.label == RF else 2) + (driver.kind == "total")
        return deepcopy(self.current["construction"][index])

    def collect(self):
        with redirect_stdout(StringIO()):
            return nc.fetch_construction_operational(self.state)

    def seed_current(self, previous=None, name="construction_operational_20261019.json"):
        self.raw.mkdir(parents=True, exist_ok=True)
        target = self.raw / name
        target.write_text(json.dumps(previous or payload(), ensure_ascii=True, indent=3), encoding="utf-8")
        record_download(target, source_id="construction_operational", remote_date="19.10.2026")
        self.state["construction_operational"] = {"filename": name, "complete": True, "sentinel": [1, 2]}
        nc.save_state(self.state)
        return target

    def snapshot(self):
        return ({str(path.relative_to(self.root)): path.read_bytes()
                 for path in self.root.rglob("*") if path.is_file()}, deepcopy(self.state))

    def assert_failed_unchanged(self):
        before = self.snapshot()
        self.assertEqual(self.collect(), ([], False))
        self.assertEqual(self.snapshot(), before)

    def test_original_current_urls_regions_modes_and_source_receipt(self):
        write_json = nc._write_json_atomic

        def publish(path, result):
            self.assertEqual((self.load.call_count, self.mode.call_count, self.read.call_count, self.parse.call_count),
                             (4, 4, 4, 2))
            nc._validate_construction_operational_result(result)
            self.assertNotIn("construction_operational", self.state)
            write_json(path, result)

        with patch.object(nc, "_write_json_atomic", side_effect=publish) as write:
            files, ok = self.collect()
        target = self.raw / "construction_operational_20261019.json"
        self.assertEqual((files, ok), ([target], True))
        self.factory.assert_called_once_with(download_dir=self.raw, headless=nc.HEADLESS)
        self.load.assert_has_calls([
            call(self.driver, nc._build_construction_indicators_url(), "construction", RF),
            call(self.driver, nc._build_construction_indicators_url(), "construction", MOSCOW),
            call(self.driver, nc._build_apartment_sales_url(), "sales", RF),
            call(self.driver, nc._build_apartment_sales_url(), "sales", MOSCOW),
        ])
        self.mode.assert_has_calls([call(self.driver, mode) for mode in (LIVING, TOTAL, LIVING, TOTAL)])
        for call_args in self.load.call_args_list:
            url = urlsplit(call_args.args[1])
            self.assertEqual(url.query, "")
            self.assertEqual(url.fragment, "")
            expected = ("/\u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430/\u043f\u043e\u043a\u0430\u0437\u0430\u0442\u0435\u043b\u0438_\u0436\u0438\u043b\u0438\u0449\u043d\u043e\u0433\u043e_\u0441\u0442\u0440\u043e\u0438\u0442\u0435\u043b\u044c\u0441\u0442\u0432\u0430"
                        if call_args.args[2] == "construction" else
                        "/\u0430\u043d\u0430\u043b\u0438\u0442\u0438\u043a\u0430/\u0440\u0435\u0430\u043b\u0438\u0437\u0430\u0446\u0438\u044f_\u0441\u0442\u0440\u043e\u044f\u0449\u0438\u0445\u0441\u044f_\u043a\u0432\u0430\u0440\u0442\u0438\u0440")
            self.assertEqual(unquote(url.path), expected)
        saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["construction"], self.current["construction"])
        self.assertEqual(saved["sales"], self.current["sales"])
        receipt = json.loads(receipt_path(target).read_text(encoding="utf-8"))
        self.assertEqual(receipt["source_id"], "construction_operational")
        self.assertEqual(receipt["sha256"], hashlib.sha256(target.read_bytes()).hexdigest())
        self.assertEqual(receipt["remote_date"], "19.10.2026")
        self.assertIsNotNone(datetime.fromisoformat(receipt["downloaded_at"]).tzinfo)
        self.assertEqual(self.state["construction_operational"]["filename"], target.name)
        self.assertTrue(self.state["construction_operational"]["complete"])
        self.assertEqual(self.state["monitoring_2_0"], {"filename": "unrelated.xlsx"})
        write.assert_called_once()
        self.driver.quit.assert_called_once()
        self.http.assert_not_called()

    def test_current_does_not_import_or_invoke_history_or_api_transport(self):
        sources = dict(nc.SOURCE_FUNCS)
        with patch.dict(sys.modules, {"pipeline.downloaders.nashdom_operational_history": None,
                                      "pipeline.downloaders.nashdom_operational_api": None}):
            files, ok = self.collect()
        self.assertTrue(ok)
        self.assertEqual([path.name for path in files], ["construction_operational_20261019.json"])
        self.assertEqual(set(self.raw.rglob("*.json")), {files[0], receipt_path(files[0])})
        self.assertEqual(nc.SOURCE_FUNCS, sources)
        self.http.assert_not_called()

    def test_every_failed_region_preserves_current_receipt_and_state_bytes(self):
        self.seed_current()
        for page in ("construction", "sales"):
            for label in (RF, MOSCOW):
                with self.subTest(page=page, region=label):
                    self.failed_region = (page, label)
                    self.assert_failed_unchanged()

    def test_every_failed_area_mode_preserves_current_receipt_and_state_bytes(self):
        self.seed_current()
        for label in (RF, MOSCOW):
            for mode in (LIVING, TOTAL):
                with self.subTest(region=label, mode=mode):
                    self.failed_mode = (label, mode)
                    self.assert_failed_unchanged()

    def test_all_snapshots_are_validated_before_any_publication(self):
        self.seed_current()
        baseline = deepcopy(self.current)
        for block, index, key, value in (
            ("construction", 3, "area_thousand_m2", ""),
            ("construction", 3, "area_kind", "living"),
            ("construction", 0, "region", MOSCOW),
            ("construction", 2, "report_period", period(2026, 9)),
            ("construction", 0, "report_date", "31.02.2026"),
            ("sales", 1, "metrics", {}),
            ("sales", 1, "report_period", period(2026, 8)),
        ):
            with self.subTest(block=block, key=key), patch.object(nc, "_write_json_atomic") as publish:
                self.current = deepcopy(baseline)
                self.current[block][index][key] = value
                self.assert_failed_unchanged()
                publish.assert_not_called()

    def test_missing_source_dates_are_not_fabricated(self):
        for block in ("construction", "sales"):
            for row in self.current[block]:
                row["report_date"] = ""
        files, ok = self.collect()
        self.assertTrue(ok)
        saved = json.loads(files[0].read_text(encoding="utf-8"))
        self.assertEqual({row["report_date"] for block in ("construction", "sales") for row in saved[block]}, {""})
        self.assertEqual(self.state["construction_operational"]["report_date"], "")
        self.assertIsNone(json.loads(receipt_path(files[0]).read_text(encoding="utf-8"))["remote_date"])

    def test_independent_latest_periods_and_dates_use_chronological_state_date(self):
        for construction_month, sales_month in ((10, 9), (9, 10)):
            with self.subTest(construction=construction_month, sales=sales_month):
                for block, month in (("construction", construction_month), ("sales", sales_month)):
                    for row in self.current[block]:
                        row["report_period"] = period(2026, month)
                # Separate runs must not be compared against the other scenario.
                for file in self.raw.rglob("*.json"):
                    file.unlink()
                files, ok = self.collect()
                self.assertTrue(ok)
                saved = json.loads(files[0].read_text(encoding="utf-8"))
                self.assertEqual(saved["construction"], self.current["construction"])
                self.assertEqual(saved["sales"], self.current["sales"])
                self.assertEqual(self.state["construction_operational"]["report_date"], "19.10.2026")

    def test_rollback_of_either_block_rejects_whole_publication(self):
        for block in ("construction", "sales"):
            with self.subTest(block=block):
                previous = payload()
                for row in previous[block]:
                    row["report_period"] = period(2026, 11 if block == "construction" else 10)
                other = "sales" if block == "construction" else "construction"
                for row in previous[other]:
                    row["report_period"] = period(2026, 8)
                self.seed_current(previous)
                self.assert_failed_unchanged()

    def test_valid_current_is_protected_even_when_newer_filename_and_state_are_corrupt(self):
        previous = payload()
        for row in previous["construction"]:
            row["report_period"] = period(2026, 11)
        self.seed_current(previous, name="construction_operational_20261001.json")
        target = self.raw / "construction_operational_20261019.json"
        target.write_text("{incomplete", encoding="utf-8")
        self.state["construction_operational"]["filename"] = target.name
        nc.save_state(self.state)
        self.assert_failed_unchanged()

    def test_newer_periods_are_compared_by_calendar_not_russian_text(self):
        for old_year, old_month, new_year, new_month in ((2026, 10, 2026, 11), (2025, 12, 2026, 1)):
            with self.subTest(old=(old_year, old_month), new=(new_year, new_month)):
                previous = payload()
                for block in ("construction", "sales"):
                    for row in previous[block]:
                        row["report_period"] = period(old_year, old_month)
                    for row in self.current[block]:
                        row["report_period"] = period(new_year, new_month)
                self.seed_current(previous)
                self.assertTrue(self.collect()[1])

    def test_invalid_source_periods_preserve_valid_current(self):
        self.seed_current()
        for invalid in ("", "unknown 2026", "2026-13", "2026-00", "2026-1", "2026", "2101-01"):
            with self.subTest(period=invalid):
                for row in self.current["construction"]:
                    row["report_period"] = invalid
                self.assert_failed_unchanged()

    def test_atomic_json_failure_preserves_current_and_state(self):
        self.seed_current()
        with patch.object(nc, "_write_json_atomic", side_effect=OSError("disk full")):
            self.assert_failed_unchanged()

    def test_receipt_failure_rolls_back_current_receipt_and_state(self):
        self.seed_current()
        self.current["construction"][0]["area_thousand_m2"] = "122000"

        def fail_after_receipt(path, **kwargs):
            record_download(path, **kwargs)
            raise OSError("receipt publication failed")

        with patch("pipeline.download_provenance.record_download", side_effect=fail_after_receipt):
            self.assert_failed_unchanged()

    def test_browser_start_failure_and_webdriver_failure_preserve_current(self):
        self.seed_current()
        for stage in ("start", "read"):
            with self.subTest(stage=stage):
                mock = self.factory if stage == "start" else self.read
                with patch.object(mock, "side_effect", WebDriverException("browser unavailable")):
                    self.assert_failed_unchanged()

    def test_replacement_browser_is_used_and_closed(self):
        replacement = Mock(name="replacement_browser")
        original_load = self.load.side_effect

        def replace_browser(driver, url, page, label):
            return original_load(replacement, url, page, label)

        self.load.side_effect = replace_browser
        replacement.quit.side_effect = WebDriverException("already closed")
        self.assertTrue(self.collect()[1])
        replacement.quit.assert_called_once()
        self.read.assert_has_calls([call(replacement)] * 4)
        self.parse.assert_has_calls([call(sales_html(row), row["region"]) for row in self.current["sales"]])


if __name__ == "__main__":
    unittest.main()
