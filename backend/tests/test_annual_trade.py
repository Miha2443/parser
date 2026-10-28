"""Trade remains independent of the automatic leading-branch display cutoff."""
import unittest

import pandas as pd

from backend.tests import test_annual as fixtures
from pipeline.annual_calculations import BRANCH_LABELS, _monitoring_nonres_branch_row, annual_charts
from pipeline.renovation import renovation_living_by_year


class AnnualTradeTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.AnnualForecastTests()
        self.fixture.setUp()
        self.data = self.fixture.data

    def test_actual_source_trade_classification_is_standalone_only_and_exclusive(self):
        frame = pd.DataFrame([{"Год ввода по Мосстату": 2026, "Отрасли": label,
                               "category_нежилое_отдельное": area, "category_нежилое_в_жилом": embedded}
                              for label, area, embedded in (("Торгово-бытовые объекты", 120000, 900000),
                                                            ("Торговые объекты", 80000, 0),
                                                            ("Промышленно-торговые объекты", 30000, 0),
                                                            ("Специальные объекты", 10000, 0))])
        row = _monitoring_nonres_branch_row(frame, 2026)
        self.assertEqual(row["торговля"], .2)
        self.assertEqual(row["пром"], .03)
        self.assertAlmostEqual(sum(row[key] for key in BRANCH_LABELS), .23)

    def test_small_trade_is_not_lumped_in_other_and_all_years_reconcile(self):
        branches = {key: .2 for key in BRANCH_LABELS}
        branches["торговля"] = .001
        self.data["load_vvod_static"]["msk_nonres_branches"] = pd.DataFrame([{"year": year, **branches} for year in range(2011, 2026)])
        original = self.data["load_vvod_static"]["msk_nonres_branches"].copy(deep=True)
        chart = next(chart for chart in annual_charts(self.data, "Москва") if chart["id"] == "b3_3")
        trade = next(series for series in chart["series"] if series["id"] == "торговля")
        self.assertEqual(trade["name"], "Торговые объекты")
        self.assertEqual(trade["points"][14]["y"], .001)
        self.assertEqual(sum(series["id"] == "торговля" for series in chart["series"]), 1)
        for row in chart["rows"]:
            expected = .2 if row["year"] == 2026 else 2
            self.assertAlmostEqual(sum(row[series["id"]] for series in chart["series"]), expected)
        pd.testing.assert_frame_equal(self.data["load_vvod_static"]["msk_nonres_branches"], original)

    def test_annual_renovation_uses_the_shared_exact_series(self):
        chart = next(chart for chart in annual_charts(self.data, "Москва") if chart["id"] == "b2_3")
        expected = renovation_living_by_year(self.data["load_monitoring_2_0"]["rv"], 2026).set_index("year")["Реновация"].to_dict()
        self.assertEqual({row["year"]: row["Реновация"] for row in chart["rows"]}, expected)


if __name__ == "__main__":
    unittest.main()
