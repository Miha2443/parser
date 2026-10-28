"""Explicit housing area variants leave approved legacy permit controls intact."""
import copy
import hashlib
from io import BytesIO
import unittest
from unittest.mock import patch

from openpyxl import Workbook, load_workbook

from backend.tests import test_construction_pages as fixtures
from backend.permits_page_service import PermitsPageService
from backend.construction_pages_export import export_workbook
from pipeline.permit_page_calculations import FORECAST_SHEET, HOUSING_SHEET, OKS_SHEET, year_over_year


class PermitAreaTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConstructionPagesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.partial_current_book()

    @staticmethod
    def row(payload, variant, year):
        return next(row for row in payload["areaVariants"][variant]["permits"]["presentation"]["rows"] if row["year"] == year)

    def test_living_and_full_include_only_the_selected_housing_area_and_standalone_nonres(self):
        for kind in ("housing", "total", "nonresidential"):
            payload = self.fixture.permits.report(kind, 5)
            nonres = sum((40000 + month) / 1000 for month in range(1, 6))
            living = sum((60000 + month) / 1000 for month in range(1, 6))
            general = sum((100000 + month) / 1000 for month in range(1, 6))
            self.assertAlmostEqual(self.row(payload, "living", 2026)["selected"], nonres if kind == "nonresidential" else living + (nonres if kind == "total" else 0))
            self.assertAlmostEqual(self.row(payload, "full", 2026)["selected"], nonres if kind == "nonresidential" else general + (nonres if kind == "total" else 0))
            self.assertEqual(self.row(payload, "living", 2026)["annualForecast"], 6850.987654321 if kind == "nonresidential" else 5810.123456789 + (6850.987654321 if kind == "total" else 0))
            self.assertEqual(self.row(payload, "full", 2026)["annualForecast"], 6850.987654321 if kind == "nonresidential" else 10017.24138 + (6850.987654321 if kind == "total" else 0))

    def test_confirmed_living_and_general_years_never_relabel_legacy_controls(self):
        payload = self.fixture.permits.report("housing", 12)
        self.assertEqual(payload["permits"]["presentation"]["rows"][0]["selected"], 9999.123456789)
        self.assertAlmostEqual(self.row(payload, "living", 2011)["selected"], sum((41463.8 + month - 1) / 1000 for month in range(1, 13)))
        self.assertAlmostEqual(self.row(payload, "full", 2011)["selected"], sum((61021.2 + month - 1) / 1000 for month in range(1, 13)))
        total = self.fixture.permits.report("total", 12)
        for variant in ("living", "full"):
            self.assertAlmostEqual(self.row(total, variant, 2011)["selected"], self.row(payload, variant, 2011)["selected"] + 7777.123456789)

    def test_missing_older_living_stays_missing_even_with_approved_annual_control(self):
        self.fixture.edit_book(HOUSING_SHEET, {"D5": None})
        payload = self.fixture.permits.report("housing", 12)
        self.assertIsNone(self.row(payload, "living", 2011)["selected"])
        self.assertIsNone(self.row(payload, "living", 2011)["annualActual"])
        self.assertIsNotNone(self.row(payload, "full", 2011)["selected"])
        self.assertEqual(payload["permits"]["presentation"]["rows"][0]["selected"], 9999.123456789)
        total = self.fixture.permits.report("total", 12)
        self.assertIsNone(self.row(total, "living", 2011)["selected"])

    def test_static_approved_annual_controls_remain_exact_and_do_not_fill_missing_component_history(self):
        base = self.fixture.root / "data/raw/realty/vvod"
        base.mkdir(parents=True)
        book = Workbook()
        book.active.append([None, "рс жильё", "ввод жилья", None, None, "рс нежильё", "ввод нежилья"])
        book.active.append([2011, 2972.123, 2200, None, 2011, 3909.456, 3553])
        book.save(base / "static_vvod_rs_2011_2025.xlsx")
        book.close()
        self.fixture.edit_book(HOUSING_SHEET, {"D5": None})
        payload = self.fixture.permits.report("housing", 12)
        self.assertEqual(payload["permits"]["presentation"]["rows"][0]["annualActual"], 2972.123)
        self.assertIsNone(self.row(payload, "living", 2011)["annualActual"])
        nonres = self.fixture.permits.report("nonresidential", 12)
        self.assertEqual(self.row(nonres, "living", 2011)["selected"], 3909.456)

    def test_full_area_does_not_require_living_and_living_does_not_require_general(self):
        self.fixture.edit_book(OKS_SHEET, {"R26": None})
        payload = self.fixture.permits.report("housing", 1)
        self.assertIsNone(self.row(payload, "living", 2026)["selected"])
        self.assertEqual(self.row(payload, "full", 2026)["selected"], 100.001)
        self.fixture.edit_book(OKS_SHEET, {"R26": 60001, "Q26": None})
        payload = self.fixture.permits.report("housing", 1)
        self.assertEqual(self.row(payload, "living", 2026)["selected"], 60.001)
        self.assertIsNone(self.row(payload, "full", 2026)["selected"])

    def test_growth_compares_same_complete_period_not_annual_or_forecast(self):
        payload = self.fixture.permits.report("housing", 5, structure_month_to=5)
        row = self.row(payload, "living", 2026)
        previous = self.row(payload, "living", 2025)
        self.assertAlmostEqual(row["growth"], (row["selected"] / previous["selected"] - 1) * 100)
        before = copy.deepcopy(payload["areaVariants"])
        self.fixture.edit_book(FORECAST_SHEET, {"D3": 999999})
        after = self.fixture.permits.report("housing", 5)
        self.assertEqual(self.row(after, "living", 2026)["growth"], row["growth"])
        self.assertEqual(self.row(after, "living", 2026)["selected"], row["selected"])
        self.assertEqual(before["living"]["permits"]["presentation"]["rows"][-1]["selected"], row["selected"])
        partial = self.fixture.permits.report("housing", 12)
        self.assertIsNone(self.row(partial, "living", 2026)["growth"])

    def test_breakdown_growth_uses_authoritative_full_annual_values_or_same_period(self):
        for mode, end in (("annual", 5), ("period", 5), ("period", 12)):
            payload = self.fixture.permits.report("total", 5, structure_mode=mode, structure_month_to=end)
            for block in payload["breakdowns"]:
                indexed = {row["year"]: row for row in block["rows"]}
                for row in block["rows"]:
                    previous = indexed.get(row["year"] - 1, {})
                    flag = "annualComplete" if mode == "annual" else "periodComplete"
                    value, denominator = row[block["aggregateField"]], previous.get(block["aggregateField"])
                    if row[flag] and previous.get(flag) and value is not None and denominator not in (None, 0):
                        self.assertAlmostEqual(row["growth"], (value / denominator - 1) * 100)
                    else:
                        self.assertIsNone(row["growth"])

    def test_zero_null_denominators_and_non_adjacent_years_are_missing(self):
        for denominator in (None, 0):
            rows = [{"year": 2024, "value": denominator, "periodComplete": True}, {"year": 2025, "value": 10, "periodComplete": True}, {"year": 2027, "value": 20, "periodComplete": True}]
            year_over_year(rows, "value")
            self.assertTrue(all(row["growth"] is None for row in rows))

    def test_semantic_revision_invalidates_old_export_versions_with_identical_source_files(self):
        payload = self.fixture.permits.report("housing", 5)
        source_only = hashlib.sha256(repr(self.fixture.permits._inputs()[1]).encode()).hexdigest()
        self.assertNotEqual(payload["version"], source_only)
        with patch("backend.permits_page_service.CALCULATION_REVISION", "previous-permit-semantics"):
            previous = PermitsPageService(self.fixture.context).report("housing", 5)
        self.assertNotEqual(payload["version"], previous["version"])
        client = self.fixture.client()
        for old in (source_only, previous["version"]):
            response = client.get("/api/v1/construction/permits/export", params={"permit_kind": "housing", "month": 5, "required_version": old})
            self.assertEqual(response.status_code, 409)
        self.assertEqual(client.get("/api/v1/construction/permits/export", params={"permit_kind": "housing", "month": 5, "required_version": payload["version"]}).status_code, 200)

    def test_excel_exposes_readable_living_and_full_area_tabs_without_replacing_legacy_controls(self):
        payload = self.fixture.permits.report("housing", 5)
        book = load_workbook(BytesIO(export_workbook(payload)), data_only=True)
        for variant in ("living", "full"):
            rows = list(book[f"Permit {variant} Area"].values)
            fields = rows[0]
            expected = payload["areaVariants"][variant]["permits"]["presentation"]["rows"]
            self.assertEqual([row[fields.index("selected")] for row in rows[1:]], [row["selected"] for row in expected])
            self.assertIn(f"Permit {variant} Evidence", book.sheetnames)
        self.assertIn("Permit Presentation", book.sheetnames)
        self.assertIn("Full Payload", book.sheetnames)
        book.close()


if __name__ == "__main__":
    unittest.main()
