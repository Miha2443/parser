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
from backend.profile_service import SourceUnavailable
from docker.publication import copy_tree
from pipeline.archive_old import scan_source
from pipeline.data_access import DataAccess, DataContext
from pipeline.source_availability import source_files, verify_inventory
from pipeline.source_records import file_sha256


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

    def historical(self):
        value = self.payload("Январь 2020", "2026-10-18T08:00:00+00:00", living=10000, total=18000)
        value["source"] = "construction_history"
        for row in value["sales"]:
            row.update(report_period="Январь 2020", report_date="14.02.2020")
        return value

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
        old = self.payload(collected="2026-10-01T00:00:00+00:00", living=14000)
        self.write(self.history, old)
        self.assertEqual(self.service.report()["construction"]["metrics"]["living"], 15000)
        old["construction"].extend(self.historical()["construction"])
        old["sales"].extend(self.historical()["sales"])
        old["scraped_at"] = "2026-10-19T10:00:00+00:00"
        self.write(self.history, old)
        report = self.service.report()
        self.assertEqual(report["construction"]["metrics"]["living"], 15000)
        self.assertEqual(len(report["construction"]["history"]), 2)

    def test_fresher_record_wins_as_a_block_without_filling_from_another_snapshot(self):
        self.write(self.current, self.payload(living=15000))
        fresh = self.payload(collected="2026-10-19T10:00:00Z", living=16000, total=None)
        self.write(self.history, fresh)
        self.assertEqual(self.service.report()["construction"]["metrics"], {"living": 16000, "total": None})
        self.assertEqual(self.service.report()["construction"]["history"][0]["sourceFile"], "data/raw/realty/nashdom/construction_history.json")

    def test_history_only_keeps_zero_null_and_real_gaps(self):
        self.write(self.history, self.historical())
        payload = self.historical()
        payload["construction"][0]["area_thousand_m2"] = 0
        payload["construction"][1]["area_thousand_m2"] = None
        payload["sales"][0]["metrics"]["sold_thousand_m2"] = None
        self.write(self.history, payload)
        report = self.service.report()
        self.assertEqual(report["construction"]["metrics"], {"living": 0, "total": None})
        self.assertEqual(len(report["construction"]["history"]), 1)
        self.assertIsNone(report["sales"]["metrics"]["sold_thousand_m2"])
        self.assertIsNone(report["source"]["date"])

    def test_bad_history_clears_cached_generation_and_retry_recovers(self):
        self.write(self.current, self.payload())
        self.write(self.history, self.historical())
        original = self.service.report()
        self.history.write_text("{broken", encoding="utf-8")
        with self.assertRaises(SourceUnavailable):
            self.service.report()
        self.assertIsNone(self.service._generation)
        self.write(self.history, self.historical())
        self.assertEqual(len(self.service.report()["sales"]["history"]), 2)
        self.assertNotEqual(original["version"], self.service.report()["version"])

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
