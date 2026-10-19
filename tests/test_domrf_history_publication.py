"""Partial verified history is publishable independently of strict daily data."""
import copy
import json
import os
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from docker.coverage import validate_construction_sources
from backend.domrf_service import DomrfService
from pipeline.data_access import DataAccess, DataContext
from pipeline.downloaders.nashdom_operational_history import _digest, download_history
from scripts.check_nashdom_operational_history import FakeSource, fixture


class HistoryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "data/raw/realty/nashdom"
        result = download_history(self.raw, client=FakeSource(), progress=lambda event: None)
        self.assertTrue(result["complete"])
        self.bundle = self.raw / "construction_history.json"
        self.daily = self.raw / "construction_operational_20261019.json"
        self.daily_payload = {"source": "construction_operational", "scraped_at": "2026-10-19T09:00:00Z",
                              "construction": fixture("construction", "2026-10")["rows"],
                              "sales": fixture("sales", "2026-08")["rows"]}
        self.daily.write_text(json.dumps(self.daily_payload, ensure_ascii=False), encoding="utf-8")
        self.access = DataAccess(DataContext(self.root, use_marts=False))
        self.current = self.access.load_construction_operational()

    def validate(self):
        validate_construction_sources(self.access, self.current, require_permits=False)

    def edit(self, change):
        value = json.loads(self.bundle.read_text(encoding="utf-8"))
        change(value)
        self.bundle.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_complete_bootstrap_is_publishable(self):
        self.validate()

    def make_partial(self, value):
        value["construction"] = [row for row in value["construction"] if row["report_period"] != "Февраль 2020"]
        value["coverage"]["construction"].update(collected_months=2, missing_months=["2020-02"])
        value.update(complete=False, history_complete=False,
                     errors=[{"block": "construction", "period": "2020-02", "error": "not published"}],
                     current_publication_error="history retry failed; daily preserved")
        value["content_sha256"] = _digest({block: value[block] for block in ("construction", "sales")})

    def test_genuine_partial_history_with_run_errors_is_publishable(self):
        self.edit(self.make_partial)
        self.validate()
        report = DomrfService(self.access.context).report()
        self.assertEqual(report["construction"]["period"]["id"], "2026-10")
        self.assertTrue(any("2020-02" in row["message"] for row in report["source"]["issues"]))

    def test_run_status_does_not_invalidate_verified_observations(self):
        self.edit(lambda value: value.update(complete=False, current_publication_error="price unavailable"))
        self.validate()

    def test_daily_newer_than_history_bounds_is_publishable(self):
        self.validate()
        report = DomrfService(self.access.context).report()
        self.assertEqual([row["period"] for row in report["construction"]["history"]],
                         ["2020-01", "2020-02", "2020-03", "2026-10"])

    def test_malformed_older_daily_does_not_block_valid_current_and_history(self):
        older = self.raw / "construction_operational_20261018.json"
        older.write_text("{broken", encoding="utf-8")
        stamp = self.daily.stat().st_mtime_ns - 1_000_000_000
        os.utime(older, ns=(stamp, stamp))
        self.validate()

    def test_changed_measurement_with_old_checksum_is_rejected(self):
        self.edit(lambda value: value["construction"][0].update(area_thousand_m2=123))
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_claimed_coverage_cannot_replace_actual_observations(self):
        self.edit(lambda value: value["coverage"]["construction"].update(expected_months=4, collected_months=4))
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_partial_history_cannot_claim_full_coverage(self):
        self.edit(self.make_partial)
        self.edit(lambda value: value.update(history_complete=True))
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_missing_range_claims_must_match_actual_observations(self):
        self.edit(self.make_partial)
        self.edit(lambda value: value["coverage"]["construction"].update(missing_months=["2020-01"]))
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_valid_checksum_cannot_replace_row_period_evidence(self):
        def change(value):
            value["sales"][0]["period_evidence"]["repMonth"] = 12
            value["content_sha256"] = _digest({block: value[block] for block in ("construction", "sales")})
        self.edit(change)
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_browser_evidence_partial_is_publishable_but_changed_page_cell_is_rejected(self):
        from pipeline.downloaders.nashdom_operational_browser import REGIONS, normalize_page, page_url
        value = json.loads(self.bundle.read_text(encoding="utf-8"))
        for block, month in (("construction", "2020-03"), ("sales", "2020-02")):
            item = fixture(block, month)
            observations = []
            for row in item["rows"]:
                snapshot = {key: copy.deepcopy(row[key]) for key in (
                    ("report_period", "report_date", "area_kind", "area_thousand_m2") if block == "construction"
                    else ("report_period", "report_date", "metrics"))}
                snapshot["region"] = REGIONS[row["region_key"]]
                observations.append({"region_key": row["region_key"], "area_kind": row.get("area_kind"),
                                     "url": page_url(block), "requested_period": month, "snapshot": snapshot,
                                     "selection": {"kind": "initial_page", "latest_period": month,
                                                   "selected_period": month, "render_stable": True, "pending_requests": 0}})
            value[block] = normalize_page(block, month, observations, collected_at=item["collected_at"])
            missing = ["2020-01", "2020-02"] if block == "construction" else ["2020-01"]
            value["coverage"][block].update(collected_months=1, missing_months=missing)
        value.update(complete=False, history_complete=False)
        value["content_sha256"] = _digest({block: value[block] for block in ("construction", "sales")})
        self.bundle.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.validate()
        value["construction"][0]["page_evidence"]["snapshot"]["area_thousand_m2"] += 1
        value["content_sha256"] = _digest({block: value[block] for block in ("construction", "sales")})
        self.bundle.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        with self.assertRaises(RuntimeError):
            self.validate()

    def test_partial_history_cannot_hide_invalid_daily_raw_behind_cached_mart(self):
        self.edit(self.make_partial)
        invalid = copy.deepcopy(self.daily_payload)
        invalid["sales"][0]["metrics"]["price_per_m2_rub"] = None
        self.daily.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "current raw"):
            self.validate()

    def test_current_raw_duplicate_rows_are_rejected_independently(self):
        invalid = copy.deepcopy(self.daily_payload)
        invalid["construction"].append(copy.deepcopy(invalid["construction"][0]))
        self.daily.write_text(json.dumps(invalid, ensure_ascii=False), encoding="utf-8")
        with self.assertRaisesRegex(RuntimeError, "current raw"):
            self.validate()

    def test_backend_cannot_change_verified_history_observations(self):
        real_report = DomrfService.report
        def changed(service, region):
            report = real_report(service, region)
            report["construction"]["history"][0]["living"] = 1
            return report
        with patch.object(DomrfService, "report", changed), self.assertRaisesRegex(RuntimeError, "differs"):
            self.validate()

    def test_same_month_daily_override_is_publishable_even_when_history_is_newer_and_nullable(self):
        value = json.loads(self.bundle.read_text(encoding="utf-8"))
        value["construction"] = fixture("construction", "2026-10", stamp="2026-10-19T11:00:00Z")["rows"]
        value["sales"] = fixture("sales", "2026-08", stamp="2026-10-19T11:00:00Z")["rows"]
        for row in value["sales"]:
            row["metrics"]["price_per_m2_rub"] = None
        for block, month in (("construction", "2026-10"), ("sales", "2026-08")):
            value["coverage"][block].update(start=month, end=month, latest_available=month,
                                            expected_months=1, collected_months=1, missing_months=[])
        value["content_sha256"] = _digest({block: value[block] for block in ("construction", "sales")})
        self.bundle.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")
        self.validate()


if __name__ == "__main__":
    unittest.main()
