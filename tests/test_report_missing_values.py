"""Missing periods stay unknown while known annual measurements remain visible."""
import unittest
from unittest.mock import patch

import pandas as pd

from pipeline import construction_calculations as construction
from pipeline import operational_calculations as operational


class MissingPeriodTests(unittest.TestCase):
    def test_operational_unknown_period_and_unknown_annual_are_distinct_from_measured_zero(self):
        frame = pd.DataFrame({"Год": [2011, 2025, 2026], "За год, млн м²": [2.12345, 1.5, None],
                              "За выбранный период, млн м²": [None, 0, 0.34567],
                              "Изменение к аналогичному периоду, %": [None, None, None]})
        data = {"load_vvod_static": {"msk_total": pd.DataFrame(), "msk_nonres": pd.DataFrame()},
                "load_monitoring_2_0": {"rv": pd.DataFrame()},
                "load_monitoring_operational_history": {"housing_monthly": pd.DataFrame()}}
        options = {"months": [{"id": 8}], "defaultMonth": 8, "currentYear": 2026, "years": [2026]}
        with patch.object(operational, "housing_ytd_table", return_value=frame), \
                patch.object(operational, "nonres_ytd_table", return_value=frame), \
                patch.object(operational, "quarter_tree", return_value={key: 0 for key, _ in operational.TREE_LABELS}):
            report = operational.report(data, options)
        for table in report["tables"]:
            chart = table["chart"]
            self.assertIsNone(chart["period"][0]["value"])
            self.assertEqual(chart["remainder"][0]["value"], 2.12345)
            self.assertEqual(chart["totals"][0]["value"], 2.12345)
            self.assertEqual(chart["period"][1]["value"], 0)
            self.assertEqual(chart["remainder"][1]["value"], 1.5)
            self.assertIsNone(chart["remainder"][2]["value"])
            self.assertEqual(chart["totals"][2]["value"], 0.34567)

    def test_permit_unknown_period_and_empty_measurement_are_not_zero(self):
        permits = pd.DataFrame({"year": [2025, 2026], "month": [1, 1], "kind": ["housing", "housing"],
                                "value_thousand_m2": [None, 0]})
        annual = pd.DataFrame({"year": [2011, 2025, 2026], "kind": ["housing"] * 3,
                               "value_thousand_m2": [2000.12345, 1500.5, 1000.25]})
        data = {"load_construction_operational": {"construction": pd.DataFrame(), "sales": pd.DataFrame(),
                                                   "permits": permits, "permit_annual": annual},
                "load_rasprodannost": {}}
        report = construction.report(data, {"monthsByKind": {"housing": [1]}}, permit_kind="housing")
        chart, rows = report["permits"]["chart"], report["permits"]["rows"]
        self.assertEqual(chart["period"], [{"value": None}, {"value": None}, {"value": 0.0}])
        self.assertEqual(chart["remainder"][0]["value"], 2000.12345)
        self.assertEqual(chart["remainder"][1]["value"], 1500.5)
        self.assertEqual(chart["totals"][2]["value"], 1000.25)
        self.assertTrue(all(row["Изменение, %"] is None for row in rows))


if __name__ == "__main__":
    unittest.main()
