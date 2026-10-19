"""Durable DOM.RF history remains separate from the latest-only legacy mart."""
import copy
from datetime import datetime, timezone
from io import BytesIO
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from openpyxl import load_workbook

from backend.construction_pages_export import export_workbook
from backend.domrf_service import DomrfService
from docker.publication import copy_tree
from docker.coverage import validate_domrf_page
from pipeline.archive_old import scan_source
from pipeline.data_access import DataAccess, DataContext
from pipeline.source_availability import source_files, verify_inventory
from pipeline.source_records import file_sha256
from pipeline.downloaders.nashdom_operational_history import _digest, months
from scripts.check_nashdom_operational_history import fixture


class DomrfHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "data/raw/realty/nashdom"
        self.raw.mkdir(parents=True)
        self.current = self.raw / "construction_operational_20261019.json"
        self.history = self.raw / "construction_history.json"
        self.context = DataContext(self.root, use_marts=False)
        self.service = DomrfService(self.context, check_interval=0)
        self.mtime = int(datetime(2026, 10, 19, 10, tzinfo=timezone.utc).timestamp())

    def write(self, path, payload):
        path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
        self.mtime += 10
        os.utime(path, (self.mtime, self.mtime))

    @staticmethod
    def payload(period="Октябрь 2026", collected="2026-10-19T09:00:00+00:00", living=15640, total=26964):
        return {"source": "construction_operational", "scraped_at": collected,
                "construction": [{"region_key": region, "report_period": period,
                                  "report_date": "01.10.2026", "area_kind": kind,
                                  "area_thousand_m2": value, "collected_at": collected}
                                 for region in ("msk", "rf") for kind, value in (("living", living), ("total", total))],
                "sales": [{"region_key": region, "report_period": "Август 2026",
                           "report_date": "11.09.2026", "collected_at": collected,
                           "metrics": {"sold_thousand_m2": 7366.123456789, "sold_pct": 47,
                                       "unsold_thousand_m2": 6774, "sales_not_open_thousand_m2": 1603,
                                       "price_per_m2_rub": 441306, "funds_million_rub": 3250869}}
                          for region in ("msk", "rf")]}

    @staticmethod
    def bundle_payload(*artifacts):
        blocks = {block: [row for item in artifacts if item["block"] == block for row in item["rows"]]
                  for block in ("construction", "sales")}
        coverage = {}
        for block in blocks:
            present = {item["period"] for item in artifacts if item["block"] == block}
            upper = max(present, default="2020-01")
            expected = months("2020-01", upper)
            coverage[block] = {"start": "2020-01", "end": upper, "latest_available": upper,
                               "expected_months": len(expected), "collected_months": len(present),
                               "missing_months": sorted(set(expected) - present)}
        complete = all(not row["missing_months"] for row in coverage.values())
        return {"source": "construction_operational_history", "schemaVersion": 1, **blocks,
                "coverage": coverage, "content_sha256": _digest(blocks), "history_complete": complete,
                "complete": complete, "errors": [], "current_publication_error": None}

    def historical(self):
        return self.bundle_payload(
            fixture("construction", "2020-01", stamp="2026-10-18T08:00:00+00:00", report_date="01.01.2020"),
            fixture("sales", "2020-01", stamp="2026-10-18T08:00:00+00:00", report_date="14.02.2020"))

    def test_monthly_union_has_literal_periods_and_independent_block_dates(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.historical())
        report = self.service.report()
        self.assertEqual([r["period"] for r in report["construction"]["history"]], ["2020-01", "2026-10"])
        self.assertEqual([r["period"] for r in report["sales"]["history"]], ["2020-01", "2026-08"])
        self.assertEqual(report["construction"]["period"]["id"], "2026-10")
        self.assertEqual(report["sales"]["period"]["reportDate"], "2026-09-11")
        self.assertEqual(report["sales"]["history"][0]["reportDate"], "2020-02-14")
        self.assertEqual(report["sales"]["history"][1]["metrics"]["sold_thousand_m2"], 7366.123456789)
        self.assertIn("construction_history.json", report["source"]["files"][0])
        self.assertEqual(report["source"]["date"], "2026-10-19")

    def test_updating_bundle_does_not_make_cached_current_record_newer(self):
        self.write(self.current, self.payload(living=15000))
        old = self.bundle_payload(fixture("construction", "2026-10", stamp="2026-10-01T00:00:00+00:00"),
                                  fixture("sales", "2026-08", stamp="2026-10-01T00:00:00+00:00"))
        self.write(self.history, old)
        self.assertEqual(self.service.report()["construction"]["metrics"]["living"], 15000)
        old["construction"].extend(self.historical()["construction"])
        old["sales"].extend(self.historical()["sales"])
        old["scraped_at"] = "2026-10-19T10:00:00+00:00"
        for block in ("construction", "sales"):
            old["coverage"][block]["missing_months"].remove("2020-01")
            old["coverage"][block]["collected_months"] += 1
        old["content_sha256"] = _digest({block: old[block] for block in ("construction", "sales")})
        self.write(self.history, old)
        report = self.service.report()
        self.assertEqual(report["construction"]["metrics"]["living"], 15000)
        self.assertEqual(len(report["construction"]["history"]), 2)

    def test_newer_nullable_history_cannot_replace_daily_current_or_graph_row(self):
        self.write(self.current, self.payload(living=15000))
        sales = fixture("sales", "2026-08", stamp="2026-10-19T10:00:00Z")
        for row in sales["rows"]:
            row["metrics"]["price_per_m2_rub"] = None
        fresh = self.bundle_payload(fixture("construction", "2026-10", stamp="2026-10-19T10:00:00Z"), sales)
        self.write(self.history, fresh)
        report = self.service.report()
        self.assertEqual(report["construction"]["metrics"], {"living": 15000, "total": 26964})
        self.assertEqual(report["sales"]["metrics"]["price_per_m2_rub"], 441306)
        for block in ("construction", "sales"):
            self.assertEqual(report[block]["history"][0]["sourceFile"], self.current.relative_to(self.root).as_posix())

    def test_future_history_period_does_not_advance_current(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.bundle_payload(fixture("construction", "2026-11"), fixture("sales", "2026-11")))
        report = self.service.report()
        self.assertEqual(report["construction"]["period"]["id"], "2026-10")
        self.assertEqual(report["sales"]["period"]["id"], "2026-08")
        self.assertEqual(report["construction"]["history"][-1]["period"], "2026-11")
        self.assertTrue(any("частично" in row["message"] for row in report["source"]["issues"]))
        self.assertTrue(any("2020-01..2026-10" in row["message"] for row in report["source"]["issues"]))

    def test_history_only_has_no_fabricated_current_and_keeps_literal_history_values(self):
        payload = self.historical()
        for row in payload["sales"]:
            row["metrics"].update(sold_pct=0, price_per_m2_rub=None)
        payload["content_sha256"] = _digest({block: payload[block] for block in ("construction", "sales")})
        self.write(self.history, payload)
        report = self.service.report()
        self.assertEqual(report["construction"]["metrics"], {"living": None, "total": None})
        self.assertIsNone(report["construction"]["period"])
        self.assertEqual(len(report["construction"]["history"]), 1)
        self.assertIsNone(report["sales"]["metrics"])
        self.assertEqual(report["sales"]["history"][0]["metrics"]["sold_pct"], 0)
        self.assertIsNone(report["sales"]["history"][0]["metrics"]["price_per_m2_rub"])
        self.assertIsNone(report["source"]["date"])
        validate_domrf_page(report, "msk")

    def test_selected_daily_missing_block_cannot_inherit_from_history_or_older_daily(self):
        self.write(self.raw / "construction_operational_20261018.json", self.payload())
        current = self.payload()
        current.pop("sales")
        self.write(self.current, current)
        self.write(self.history, self.historical())
        report = self.service.report()
        self.assertIsNone(report["sales"]["period"])
        self.assertIsNone(report["sales"]["metrics"])
        self.assertEqual([row["period"] for row in report["sales"]["history"]], ["2020-01", "2026-08"])

    def test_latest_valid_daily_ignores_corrupt_daily_and_non_daily_filenames(self):
        self.write(self.current, self.payload())
        invalid = self.raw / "construction_operational_20261020.json"
        invalid.write_text("{broken", encoding="utf-8")
        self.mtime += 10
        os.utime(invalid, (self.mtime, self.mtime))
        self.write(self.raw / "construction_operational_20261021_history.json", self.payload(living=1))
        report = self.service.report()
        self.assertEqual(report["source"]["date"], "2026-10-19")
        self.assertEqual(report["construction"]["metrics"]["living"], 15640)
        self.assertTrue(any("20261020" in issue["message"] for issue in report["source"]["issues"]))

    def test_failed_history_refresh_cannot_regress_cached_daily_display(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.historical())
        original = self.service.report()
        self.history.write_text("{broken", encoding="utf-8")
        failed = self.service.report()
        for block in ("construction", "sales"):
            self.assertEqual(failed[block]["metrics"], original[block]["metrics"])
            self.assertEqual(failed[block]["period"], original[block]["period"])
            self.assertEqual(len(failed[block]["history"]), 1)
        self.assertTrue(any("не прошёл проверку" in issue["message"] for issue in failed["source"]["issues"]))
        self.write(self.history, self.historical())
        self.assertEqual(len(self.service.report()["sales"]["history"]), 2)
        self.assertNotEqual(original["version"], self.service.report()["version"])

    def test_history_with_false_coverage_is_excluded_without_changing_daily(self):
        self.write(self.current, self.payload())
        value = self.historical()
        value["coverage"]["sales"]["collected_months"] += 1
        self.write(self.history, value)
        report = self.service.report()
        self.assertEqual(report["sales"]["period"]["id"], "2026-08")
        self.assertEqual(len(report["sales"]["history"]), 1)
        self.assertTrue(report["source"]["issues"])

    def test_registry_and_publication_inventory_own_history_but_legacy_mart_does_not(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.historical())
        access = DataAccess(self.context)
        self.assertEqual(access.source_files("construction_history"), [self.history])
        self.assertNotIn(self.history, access.source_files("construction_operational"))
        self.assertEqual(set(source_files(self.root, "construction")), {self.current, self.history})
        inventory = [{"path": str(p.relative_to(self.root)), "mtime_ns": p.stat().st_mtime_ns, "sha256": file_sha256(p)}
                     for p in source_files(self.root, "construction")]
        publication = {"sources": {"construction": {"inventory": inventory}}}
        verify_inventory(self.root, publication)
        payload = self.historical()
        payload["construction"][0]["area_thousand_m2"] = 500
        self.write(self.history, payload)
        with self.assertRaises(RuntimeError):
            verify_inventory(self.root, publication)

    def test_archive_ignores_durable_bundle_and_publication_copies_it(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.historical())
        private = self.raw / ".construction_history"
        private.mkdir()
        (private / "partial.json").write_text("private checkpoint", encoding="utf-8")
        (self.raw / ".construction_history.lock").write_text("0", encoding="utf-8")
        with patch("pipeline.archive_old.REALTY_ROOT", self.root / "data/raw/realty"):
            candidates = scan_source("nashdom")
        self.assertFalse(any(self.history in paths for paths in candidates.values()))
        published = self.root / "runtime-copy"
        copy_tree(self.raw, published)
        self.assertEqual((published / self.history.name).read_bytes(), self.history.read_bytes())
        self.assertEqual((published / self.history.name).stat().st_mtime_ns, self.history.stat().st_mtime_ns)
        self.assertFalse((published / ".construction_history").exists())
        self.assertFalse((published / ".construction_history.lock").exists())

    def test_complete_history_excel_retains_periods_precision_and_source_values(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.historical())
        report = self.service.report()
        before = copy.deepcopy(report)
        book = load_workbook(BytesIO(export_workbook(report)), data_only=False)
        self.addCleanup(book.close)
        sales = list(book["Sales History"].values)
        self.assertEqual([row[0] for row in sales[1:]], ["2020-01", "2026-08"])
        sold = sales[0].index("sold_thousand_m2")
        self.assertEqual(sales[-1][sold], 7366.123456789)
        self.assertEqual(report, before)


if __name__ == "__main__":
    unittest.main()
