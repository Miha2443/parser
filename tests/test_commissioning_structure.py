import unittest

import pandas as pd

from pipeline.commissioning_structure import monthly_structure


class MonthlyCommissioningStructureTests(unittest.TestCase):
    def rows(self):
        rows = []
        for year in (2025, 2026):
            for month in ("Январь", "Февраль"):
                for industry, subtype, grouping, total, living in (
                    ("Жилые объекты", "МКД", "Жилье", 100000, 60000),
                    ("Жилые объекты", "1-е этажи МКД", "Нежилье", 10000, 0),
                    ("Торгово-бытовые объекты", "МПТ", "Нежилье", 20000, 0),
                    ("Социально-реабилитационные объекты", "Социалка", "Нежилье", 30000, 0),
                ):
                    housing = industry == "Жилые объекты"
                    rows.append({"Год ввода по Мосстату": year, "Месяц ввода по Мосстату": month,
                                 "Отрасли": industry, "Подтип объекта": subtype, "Группировка": grouping,
                                 "Общая площадь": total, "Жилая площадь": living,
                                 "Общая площадь_measured": True, "Жилая площадь_measured": True,
                                 "category_жилое": living if grouping == "Жилье" else 0,
                                 "category_моп": total - living if grouping == "Жилье" else 0,
                                 "category_нежилое_в_жилом": total if housing and grouping == "Нежилье" else 0,
                                 "category_нежилое_отдельное": total if not housing else 0,
                                 "Группа компаний": "Не фонд", "Инвестор для Реновации": "Да" if grouping == "Жилье" else None})
        return pd.DataFrame(rows)

    def report(self, frame=None, month=2):
        report = monthly_structure(self.rows() if frame is None else frame, 2026, month, "16.02.2026")
        return report, {row["id"]: row for row in report["rows"]}

    def test_ytd_month_change_and_renovation_are_independent_and_additive(self):
        report, rows = self.report()
        self.assertEqual(report["actualMonth"], 2)
        self.assertAlmostEqual(rows["total"]["value"], .32)
        self.assertAlmostEqual(rows["total"]["monthChange"], .16)
        self.assertEqual(rows["total"]["yearChangePct"], 0)
        self.assertAlmostEqual(rows["mkd_total"]["value"], .22)
        self.assertAlmostEqual(rows["mkd_residential"]["renovation"], .12)
        self.assertAlmostEqual(rows["mkd_residential"]["renovationMonthChange"], .06)
        self.assertEqual(rows["mkd_residential"]["renovationYearChangePct"], 0)
        self.assertEqual(rows["trade"]["value"], .04)
        self.assertEqual(rows["social"]["value"], .06)
        self.assertEqual(rows["other"]["value"], 0)

    def test_missing_general_does_not_destroy_known_month_or_invent_mop(self):
        frame = self.rows()
        frame.loc[8, "Общая площадь"] = 0
        frame.loc[8, "Общая площадь_measured"] = False
        frame.loc[8, "category_моп"] = 0
        _, rows = self.report(frame)
        self.assertIsNone(rows["total"]["value"])
        self.assertAlmostEqual(rows["total"]["knownSum"], .22)
        self.assertAlmostEqual(rows["total"]["monthChange"], .16)
        self.assertIsNone(rows["total"]["yearChangePct"])
        self.assertIsNone(rows["mop"]["value"])
        self.assertEqual(rows["mop"]["knownSum"], .04)
        self.assertIsNone(rows["mkd_total"]["value"])

    def test_requested_december_is_bounded_and_january_growth_is_month_fact(self):
        report, rows = self.report(month=12)
        self.assertEqual(report["actualMonth"], 2)
        _, january = self.report(month=1)
        self.assertEqual(january["total"]["monthChange"], january["total"]["value"])
        self.assertEqual(rows["total"]["value"], .32)

    def test_missing_year_or_renovation_attribute_remains_unknown(self):
        frame = self.rows().drop(columns=["Инвестор для Реновации"])
        _, rows = self.report(frame)
        self.assertIsNone(rows["mkd_residential"]["renovation"])
        missing = monthly_structure(frame, 2024, 12)
        self.assertIsNone(missing["actualMonth"])
        self.assertTrue(all(row["value"] is None for row in missing["rows"]))

    def test_unknown_month_invalidates_only_affected_comparisons(self):
        frame = self.rows()
        frame.loc[0, "Месяц ввода по Мосстату"] = None
        _, rows = self.report(frame)
        self.assertIsNone(rows["total"]["yearChangePct"])
        self.assertIsNone(rows["mkd_residential"]["yearChangePct"])
        self.assertEqual(rows["social"]["yearChangePct"], 0)
        frame.loc[8, "Месяц ввода по Мосстату"] = None
        _, rows = self.report(frame)
        self.assertIsNone(rows["total"]["value"])
        self.assertIsNone(rows["total"]["monthChange"])
        self.assertEqual(rows["social"]["value"], .06)

    def test_linear_roads_are_not_unknown_building_area(self):
        frame = self.rows()
        road = frame.iloc[8].copy()
        road["Отрасли"], road["Общая площадь"] = "Улицы и дороги", 0
        road["Общая площадь_measured"] = False
        frame = pd.concat([frame, pd.DataFrame([road])], ignore_index=True)
        _, rows = self.report(frame)
        self.assertAlmostEqual(rows["total"]["value"], .32)
        self.assertAlmostEqual(rows["total"]["monthChange"], .16)

    def test_known_components_and_incomplete_renovation_are_not_lost(self):
        frame = self.rows()
        for index in (9, 13):
            frame.loc[index, "Общая площадь_measured"] = False
            frame.loc[index, "category_нежилое_в_жилом"] = 0
        _, rows = self.report(frame)
        self.assertIsNone(rows["mkd_total"]["value"])
        self.assertAlmostEqual(rows["mkd_total"]["knownSum"], .2)
        frame = self.rows()
        frame.loc[8, "Общая площадь_measured"] = False
        _, rows = self.report(frame)
        self.assertIsNone(rows["housing_objects"]["renovation"])
        self.assertAlmostEqual(rows["housing_objects"]["renovationKnownSum"], .1)
        self.assertAlmostEqual(rows["housing_objects"]["renovationMonthChange"], .1)

    def test_old_mart_without_measurement_flags_cannot_claim_exact_values(self):
        frame = self.rows().drop(columns=["Общая площадь_measured", "Жилая площадь_measured"])
        report, rows = self.report(frame)
        self.assertTrue(report["issues"])
        self.assertIsNone(rows["total"]["value"])
        self.assertIsNone(rows["total"]["knownSum"])
        self.assertIsNone(rows["total"]["yearChangePct"])


if __name__ == "__main__":
    unittest.main()
