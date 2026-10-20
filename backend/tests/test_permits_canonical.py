"""Approved permit controls, complete month selection and honest compositions."""
import unittest

from openpyxl import Workbook

from backend.tests import test_construction_pages as fixtures
from pipeline.permit_page_calculations import FORECAST_SHEET, HISTORY_SHEET, HOUSING_SHEET, OKS_SHEET


class CanonicalPermitTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConstructionPagesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.service = self.fixture.permits
        self.fixture.partial_current_book()

    @staticmethod
    def main(payload, year):
        return next(row for row in payload["permits"]["presentation"]["rows"] if row["year"] == year)

    @staticmethod
    def block(payload, identifier, year):
        block = next(block for block in payload["breakdowns"] if block["id"] == identifier)
        return next(row for row in block["rows"] if row["year"] == year)

    def test_december_available_with_current_october_and_approved_full_previous_years(self):
        payload = self.service.report("housing", 12, structure_mode="annual")
        self.assertEqual(payload["controls"]["monthsByKind"]["housing"], list(range(1, 13)))
        self.assertEqual(payload["controls"]["structure"]["months"], list(range(1, 13)))
        self.assertEqual(payload["selection"]["month"], 12)
        old = self.main(payload, 2011)
        self.assertEqual(old["selected"], 9999.123456789)
        self.assertEqual(old["actualMonth"], 12)
        self.assertTrue(old["periodComplete"])
        current = self.main(payload, 2026)
        self.assertAlmostEqual(current["selected"], sum((60000 + month) / 1000 for month in range(1, 11)))
        self.assertEqual(current["actualMonth"], 10)
        self.assertFalse(current["periodComplete"])
        self.assertEqual(current["observedThrough"], "2026-10-16")
        self.assertEqual(self.block(payload, "housing_components", 2026)["actualMonth"], 10)

    def test_latest_measurement_not_snapshot_month_bounds_selected_year(self):
        self.fixture.edit_book(OKS_SHEET, {"B44": None, "B45": None})
        payload = self.service.report("total", 12, structure_mode="annual")
        current = self.main(payload, 2026)
        self.assertEqual(current["actualMonth"], 9)
        self.assertAlmostEqual(current["selected"], sum((140000 + 2 * month) / 1000 for month in range(1, 10)))
        for block in payload["breakdowns"]:
            row = next(row for row in block["rows"] if row["year"] == 2026)
            self.assertEqual(row["actualMonth"], 9)
            self.assertIsNotNone(row[block["aggregateField"]])

    def test_main_and_structure_share_approved_controls_without_rescaling_mop(self):
        for kind, field, block in (("housing", "living", "housing_components"),
                                   ("nonresidential", "nonresidential", "nonresidential_components"),
                                   ("total", "total", "area_composition")):
            payload = self.service.report(kind, 12, structure_mode="annual")
            for year in (2011, 2018, 2021, 2025, 2026):
                self.assertAlmostEqual(self.main(payload, year)["selected"], self.block(payload, block, year)[field])
        payload = self.service.report("total", 12, structure_mode="annual")
        old = self.block(payload, "housing_components", 2011)
        self.assertEqual(old["living"], 9999.123456789)
        self.assertAlmostEqual(old["mop_embedded"], 12 * (61021.2 - 41463.8) / 1000)
        self.assertAlmostEqual(old["source_living"], sum((41463.8 + month - 1) / 1000 for month in range(1, 13)))
        self.assertNotEqual(old["housing_total"], old["source_housing_total"])

    def test_approved_static_precedes_workbook_in_both_views(self):
        directory = self.fixture.root / "data/raw/realty/vvod"
        directory.mkdir(parents=True)
        path = directory / "static_vvod_rs_2011_2025.xlsx"
        book = Workbook()
        book.active.append(["year", "permits", "input", "", "year", "permits", "input"])
        book.active.append([2011, 1234.5, 0, "", 2011, 4567.8, 0])
        book.save(path)
        book.close()
        self.fixture.touch(path)
        for kind, block, field, value in (("housing", "housing_components", "living", 1234.5),
                                           ("nonresidential", "nonresidential_components", "nonresidential", 4567.8)):
            payload = self.service.report(kind, 12, structure_mode="annual")
            self.assertEqual(self.main(payload, 2011)["selected"], value)
            self.assertEqual(self.block(payload, block, 2011)[field], value)

    def test_total_contains_mop_in_selected_fact_and_forecast(self):
        payload = self.service.report("total", 5, structure_month_to=5)
        self.assertAlmostEqual(self.main(payload, 2026)["selected"], sum((140000 + 2 * month) / 1000 for month in range(1, 6)))
        self.assertEqual(self.main(payload, 2026)["annualForecast"], 10017.24138 + 6850.987654321)
        self.fixture.edit_book(FORECAST_SHEET, {"D4": None, "D5": None})
        self.assertIsNone(self.main(self.service.report("total", 5), 2026)["annualForecast"])

    def test_missing_historic_mop_never_invented_from_living_annual_control(self):
        self.fixture.edit_book(HOUSING_SHEET, {"C5": None})
        housing = self.service.report("housing", 12, structure_mode="annual")
        self.assertEqual(self.main(housing, 2011)["selected"], 9999.123456789)
        self.assertIsNone(self.block(housing, "housing_components", 2011)["mop_embedded"])
        total = self.service.report("total", 12, structure_mode="annual")
        self.assertIsNone(self.main(total, 2011)["selected"])
        self.assertIsNone(self.block(total, "area_composition", 2011)["total"])

    def test_latest_living_month_survives_missing_general_area_in_both_structure_modes(self):
        self.fixture.edit_book(OKS_SHEET, {"Q44": None})
        for mode in ("annual", "period"):
            with self.subTest(mode=mode):
                payload = self.service.report("housing", 12, structure_mode=mode, structure_month_to=12)
                main = self.main(payload, 2026)
                structure = self.block(payload, "housing_components", 2026)
                self.assertEqual(main["actualMonth"], 10)
                self.assertEqual(structure["actualMonth"], 10)
                self.assertEqual(structure["living"], main["selected"])
                self.assertAlmostEqual(structure["living"], sum((60000 + month) / 1000 for month in range(1, 11)))
                self.assertIsNone(structure["mop_embedded"])
                self.assertIsNone(structure["housing_total"])

    def test_one_nonresidential_chart_keeps_unmapped_categories_and_undetailed_controls(self):
        payload = self.service.report("nonresidential", 12, structure_mode="annual")
        self.assertEqual([block["id"] for block in payload["breakdowns"]], ["nonresidential_components"])
        block = payload["breakdowns"][0]
        self.assertEqual([row["year"] for row in block["rows"]], [*range(2011, 2022), 2025, 2026])
        old, modern = self.block(payload, block["id"], 2011), self.block(payload, block["id"], 2026)
        self.assertEqual(old["undetailed"], 7777.123456789)
        self.assertIsNone(old["offices"])
        self.assertAlmostEqual(old["source_offices"], 234.535 * 12)
        native = next(column["id"] for column in block["columns"] if column["label"] == "Минстрой: Коммерческие")
        self.assertIsNone(old[native])
        self.assertIsNotNone(modern[native])
        self.assertIsNone(modern["trade"])
        for index, row in enumerate(block["rows"]):
            values = [series["points"][index]["y"] for series in block["series"]]
            self.assertAlmostEqual(sum(value for value in values if value is not None), row["nonresidential"])

    def test_periods_use_category_total_not_incompatible_auxiliary_nonresidential(self):
        payload = self.service.report("nonresidential", 5, structure_month_to=5)
        old = self.main(payload, 2011)
        self.assertAlmostEqual(old["selected"], 327.442 * 5)
        self.assertEqual(old["selected"], self.block(payload, "nonresidential_components", 2011)["nonresidential"])
        self.assertNotAlmostEqual(old["selected"], sum((348192.6 + month - 1) / 1000 for month in range(1, 6)))


if __name__ == "__main__":
    unittest.main()
