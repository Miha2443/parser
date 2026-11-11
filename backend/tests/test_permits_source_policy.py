"""Permit history stops at 2021; all subsequent facts come from OKS."""
from datetime import datetime
import unittest

from openpyxl import load_workbook

from backend.tests import test_construction_pages as fixtures
from pipeline.permit_page_calculations import HISTORY_SHEET, HOUSING_SHEET, OKS_SHEET


class PermitSourcePolicyTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConstructionPagesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.fixture.partial_current_book()
        self.service = self.fixture.permits

    @staticmethod
    def main(payload, variant, year):
        return next(row for row in payload["areaVariants"][variant]["permits"]["presentation"]["rows"]
                    if row["year"] == year)

    @staticmethod
    def block(payload, identifier, year):
        block = next(row for row in payload["breakdowns"] if row["id"] == identifier)
        return next(row for row in block["rows"] if row["year"] == year)

    def assert_amount(self, first, second):
        if first is None or second is None:
            self.assertIs(first, second)
        else:
            self.assertAlmostEqual(first, second)

    def test_every_month_and_both_annual_modes_share_all_three_components(self):
        for kind in ("housing", "nonresidential", "total"):
            for month in range(1, 13):
                modes = ("period", "annual") if month == 12 else ("period",)
                for mode in modes:
                    payload = self.service.report(kind, month, structure_mode=mode, structure_month_to=month)
                    for year in (*range(2011, 2022), 2025, 2026):
                        for variant in ("living", "full"):
                            with self.subTest(kind=kind, month=month, mode=mode, year=year, variant=variant):
                                if kind == "housing":
                                    row = self.block(payload, "housing_components", year)
                                    expected = row["living" if variant == "living" else "housing_total"]
                                elif kind == "nonresidential":
                                    expected = self.block(payload, "nonresidential_components", year)["nonresidential"]
                                else:
                                    row = self.block(payload, "area_composition", year)
                                    expected = (row["living"] + row["nonresidential"]
                                                if row["living"] is not None and row["nonresidential"] is not None else None)
                                    if variant == "full":
                                        expected = row["total"]
                                self.assert_amount(self.main(payload, variant, year)["selected"], expected)

    def test_2021_uses_typed_history_and_2022_uses_only_registry(self):
        book = load_workbook(self.fixture.book_path)
        history, registry = book[HISTORY_SHEET], book[OKS_SHEET]
        for header, first, annual, base in ((63, 64, 76, 100), (80, 81, 93, 200), (97, 98, 110, 300)):
            history.cell(header, 17, 2021)
            for month in range(1, 13):
                history.cell(first + month - 1, 17, base + month)
            history.cell(annual, 17, sum(base + month for month in range(1, 13)))
        # Contradicting older controls must not override the registry.
        history["B10"], history["C10"] = 2022, 99999999
        history["B5"], history["C5"] = 2022, 88888888
        for year in (2021, 2022):
            for month in range(1, 13):
                for purpose in ("Жилье", "Нежилье"):
                    row = registry.max_row + 1
                    for column, value in {2: purpose, 4: datetime(year, month, 2), 6: year,
                                          17: (700000 if purpose == "Жилье" else 300000) + month,
                                          18: 500000 + month if purpose == "Жилье" else None}.items():
                        registry.cell(row, column, value)
        book.save(self.fixture.book_path)
        book.close()
        self.fixture.touch(self.fixture.book_path)
        for end in (5, 12):
            for kind, historical, modern in (("housing", 100, 500000), ("nonresidential", 300, 300000)):
                payload = self.service.report(kind, end, structure_month_to=end)
                self.assertAlmostEqual(self.main(payload, "living", 2021)["selected"], sum(historical + m for m in range(1, end + 1)))
                self.assertAlmostEqual(self.main(payload, "living", 2022)["selected"], sum((modern + m) / 1000 for m in range(1, end + 1)))
            payload = self.service.report("housing", end, structure_month_to=end)
            self.assertAlmostEqual(self.main(payload, "full", 2021)["selected"], sum(200 + m for m in range(1, end + 1)))
            self.assertAlmostEqual(self.main(payload, "full", 2022)["selected"], sum((700000 + m) / 1000 for m in range(1, end + 1)))

    def test_auxiliary_sheet_and_untyped_controls_never_affect_either_chart(self):
        before = self.service.report("total", 5, structure_month_to=5)
        self.fixture.edit_book(HOUSING_SHEET, {"C5": 99999999, "D5": 88888888, "G5": 77777777})
        self.fixture.edit_book(HISTORY_SHEET, {"C3": 66666666, "C8": 55555555})
        after = self.service.report("total", 5, structure_month_to=5)
        for key in ("permits", "areaVariants", "breakdowns"):
            self.assertEqual(before[key], after[key])

    def test_general_minus_living_is_calculated_after_aggregation(self):
        # Some literal source months have general < living, but their sum is valid.
        self.fixture.edit_book(HISTORY_SHEET, {"G64": 50, "G65": 100, "G81": 20, "G82": 200,
                                                "G76": 900, "G93": 1200})
        period = self.service.report("housing", 2, structure_month_to=2)
        row = self.block(period, "housing_components", 2011)
        self.assertEqual((row["living"], row["housing_total"], row["mop_embedded"]), (150, 220, 70))
        self.assertTrue(row["periodComplete"])
        annual = self.service.report("housing", 12, structure_mode="annual")
        row = self.block(annual, "housing_components", 2011)
        self.assertEqual((row["living"], row["housing_total"], row["mop_embedded"]), (900, 1200, 300))
        for variant, expected in (("living", 900), ("full", 1200)):
            self.assertEqual(self.main(annual, variant, 2011)["selected"], expected)

    def test_missing_annual_cell_uses_complete_months_and_no_coefficients(self):
        self.fixture.edit_book(HISTORY_SHEET, {"G76": None, "G93": None, "G110": None})
        payload = self.service.report("total", 12, structure_mode="annual")
        row = self.block(payload, "housing_components", 2011)
        self.assertAlmostEqual(row["housing_total"], sum(61.0212 + m / 1000 for m in range(12)))
        self.assertAlmostEqual(row["living"], sum(41.4638 + m / 1000 for m in range(12)))
        self.assertAlmostEqual(row["mop_embedded"], row["housing_total"] - row["living"])
        self.assertTrue(row["annualComplete"])

    def test_bad_historical_header_is_not_replaced_by_auxiliary_data(self):
        self.fixture.edit_book(HISTORY_SHEET, {"F62": "Unconfirmed area"})
        payload = self.service.report("housing", 5, structure_month_to=5)
        self.assertIsNone(self.main(payload, "living", 2011)["selected"])
        self.assertIsNotNone(self.main(payload, "full", 2011)["selected"])
        self.assertIsNone(self.block(payload, "housing_components", 2011)["living"])

    def test_blank_latest_component_does_not_shorten_one_chart_only(self):
        self.fixture.edit_book(OKS_SHEET, {"R44": None})
        payload = self.service.report("housing", 12, structure_mode="annual")
        row = self.block(payload, "housing_components", 2026)
        self.assertEqual(row["actualMonth"], 10)
        self.assertIsNone(row["living"])
        self.assertIsNone(self.main(payload, "living", 2026)["selected"])
        self.assertEqual(self.main(payload, "living", 2026)["actualMonth"], 10)
        self.assertEqual(self.main(payload, "full", 2026)["actualMonth"], 10)
        self.assertAlmostEqual(row["housing_total"], self.main(payload, "full", 2026)["selected"])


if __name__ == "__main__":
    unittest.main()
