"""Offline collector orchestration; fixtures never enter working data."""
import copy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from pipeline.downloaders.nashdom_mortgage import (
    AccessVerificationRequired, RAW_PATH, collect, collection_lock, require_scope, year_bundle,
)
from pipeline.mortgage_history import load_published, save_checkpoint


def monthly(region, year, month, *, value=10, basis="month"):
    metrics = {"volumeRub": value * 1e9, "count": value * 1000, "ratePct": 8.5,
               "termMonths": None, "averageAmountRub": None}
    return {"region": region, "period": f"{year}-{month:02d}", "basis": basis,
            "metrics": {segment: dict(metrics) for segment in ("all", "primary", "secondary")},
            "rawMetrics": {"volume": {"value": str(value), "unit": "billion RUB"}}}


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
        result = year_bundle("msk", 2026, [(8, item)])
        self.assertEqual(result["records"][0]["termMonths"], 290.4)
        self.assertIsNone(result["records"][1]["termMonths"])
        for label in document.select(".card_item_body .item_period")[:2]:
            label.string = "за январь-август 2026 года"
        item = parse_page(str(document), expected_region="msk", expected_period="2026-08")
        self.assertIsNone(year_bundle("msk", 2026, [(8, item)])["records"][0]["termMonths"])

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


if __name__ == "__main__":
    unittest.main()
