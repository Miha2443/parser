"""Housing history must not combine similarly shaped IHS/MKD/RS blocks."""
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from backend.operational_routes import build_report
from pipeline.data_access import DataAccess, DataContext, MONTH_NAMES_RU, _parse_monitoring_housing_history
from pipeline.operational_calculations import catalog
from docker.coverage import validate_monitoring_history


def workbook_frame():
    frame = pd.DataFrame([[None] * 22 for _ in range(20)])
    for title_row, title, values in ((2, "Ввод жилья", range(10, 22)),
                                     (7, "Ввод ИЖС, тыс. кв. м", [500] * 12),
                                     (12, "Ввод МКД, тыс. кв. м", [800] * 12),
                                     (17, "Разрешения на строительство", [900] * 12)):
        frame.iloc[title_row, 5] = title
        frame.iloc[title_row + 1, 5] = "Год"
        frame.iloc[title_row + 1, 6:18] = MONTH_NAMES_RU
        frame.iloc[title_row + 2, 5] = 2011
        frame.iloc[title_row + 2, 6:18] = list(values)
    return frame


class MonitoringHousingHistoryTests(unittest.TestCase):
    def test_named_block_ignores_repeated_years_in_other_measures_and_preserves_units(self):
        frame = workbook_frame()
        before = frame.copy(deep=True)
        history = _parse_monitoring_housing_history(frame)
        self.assertEqual(len(history), 12)
        self.assertFalse(history.duplicated(["year", "month"]).any())
        self.assertEqual(history.value_thousand_m2.tolist(), list(range(10, 22)))
        pd.testing.assert_frame_equal(frame, before)

    def test_missing_named_housing_or_exact_calendar_header_remains_unknown(self):
        for change in ("title", "header", "order"):
            frame = workbook_frame()
            if change == "title":
                frame.iloc[2, 5] = "Ввод ИЖС"
            elif change == "header":
                frame.iloc[3, 6] = "Месяц"
            else:
                frame.iloc[3, 6:8] = ["февраль", "январь"]
            with self.subTest(change=change):
                self.assertTrue(_parse_monitoring_housing_history(frame).empty)

    def test_duplicate_year_month_or_ambiguous_block_is_rejected(self):
        for change in ("year", "title", "header"):
            frame = workbook_frame()
            if change == "year":
                frame.iloc[5, 5:18] = frame.iloc[4, 5:18]
            elif change == "title":
                frame.iloc[7, 5] = "Ввод жилья"
            else:
                frame.iloc[5, 5:18] = frame.iloc[3, 5:18]
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "duplicate|multiple"):
                _parse_monitoring_housing_history(frame)

    def test_missing_and_bounded_values_are_not_zero_but_measured_zero_is_preserved(self):
        frame = workbook_frame()
        frame.iloc[4, 6:10] = [None, "<1", 0, "12,5"]
        history = _parse_monitoring_housing_history(frame)
        self.assertEqual(len(history), 10)
        self.assertEqual(history.month.tolist(), list(range(3, 13)))
        self.assertEqual(history.value_thousand_m2.iloc[:2].tolist(), [0, 12.5])

    def test_publication_contract_rejects_invalid_periods_and_measurements(self):
        from types import SimpleNamespace
        frame = pd.DataFrame([{"year": 2011, "month": 1, "value_thousand_m2": 0}])
        def check(history):
            return validate_monitoring_history(SimpleNamespace(load_monitoring_operational_history=lambda: {"housing_monthly": history}))
        self.assertEqual(len(check(frame)), 1)  # A measured zero remains valid.
        for changes in ({"year": float("inf")}, {"year": 2011.5}, {"month": 0}, {"month": 13},
                        {"month": 1.5}, {"value_thousand_m2": float("nan")}, {"value_thousand_m2": -1}):
            changed = pd.DataFrame([{**frame.iloc[0].to_dict(), **changes}])
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                check(changed)

    def test_actual_loader_and_api_report_use_only_housing_ytd_without_annual_substitution(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "data/raw/realty/nashdom/monitoring_2_0_20261013.xlsx"
            source.parent.mkdir(parents=True)
            workbook_frame().to_excel(source, sheet_name="Данные с 2011 года", header=False, index=False)
            access = DataAccess(DataContext(root))
            history = access.load_monitoring_operational_history()
            self.assertEqual(history["source_date"], "13.10.2026")
            self.assertEqual(Path(history["source_file"]), source)
            rv = pd.DataFrame([{"Год ввода по Мосстату": 2026, "Месяц ввода по Мосстату": "август",
                                "Квартал ввода по Мосстату": 3, "Отрасли": "Жилые объекты", "Подтип объекта": "МКД",
                                "category_жилое": 500000, "category_нежилое_отдельное": 0,
                                "category_нежилое_в_жилом": 0, "category_моп": 0, "Общая площадь": 500000}])
            data = {"load_monitoring_operational_history": history, "load_monitoring_2_0": {"rv": rv},
                    "load_vvod_static": {"msk_total": pd.DataFrame({"year": [2011, 2012], "жильё": [2.2, 4.4]}),
                                         "msk_nonres": pd.DataFrame({"year": [2011, 2012], "общая": [1., 2.], "нежильё": [1., 2.]})}}
            report = build_report(data, catalog(data), month=7)
            rows = report["tables"][0]["rows"]
            self.assertAlmostEqual(rows[0]["За выбранный период, млн м²"], sum(range(10, 17)) / 1000)
            self.assertEqual(rows[0]["За год, млн м²"], 2.2)
            self.assertIsNone(rows[1]["За выбранный период, млн м²"])
            frame = workbook_frame()
            frame.iloc[3, 6] = "missing month header"
            frame.to_excel(source, sheet_name="Данные с 2011 года", header=False, index=False)
            data["load_monitoring_operational_history"] = access.load_monitoring_operational_history()
            unknown = build_report(data, catalog(data), month=7)["tables"][0]
            self.assertIsNone(unknown["rows"][0]["За выбранный период, млн м²"])
            self.assertEqual(unknown["chart"]["remainder"][0]["value"], 2.2)


if __name__ == "__main__":
    unittest.main()
