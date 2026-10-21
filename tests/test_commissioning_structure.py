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

    def test_missing_general_without_measured_living_remains_unknown(self):
        frame = self.rows()
        frame.loc[8, "Общая площадь"] = 0
        frame.loc[8, "Общая площадь_measured"] = False
        frame.loc[8, "Жилая площадь_measured"] = False
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
        frame.loc[8, "Жилая площадь_measured"] = False
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

    def test_missing_general_is_living_divided_by_58_percent_for_mkd_only(self):
        frame = self.rows()
        index = 12  # February 2026, so the calculation also restores monthly growth.
        frame.loc[index, ["Общая площадь", "category_моп"]] = 0
        frame.loc[index, "Общая площадь_measured"] = False
        frame.loc[index, "Наименование объекта"] = "Реконструкция жилого дома"
        frame.loc[index, "Адрес"] = "Тестовый адрес"
        frame.loc[index, "source_file"] = "monitoring.xlsx"
        frame.loc[index, "source_sheet"] = "Реестр РВ"
        frame.loc[index, "source_row"] = 4602
        before = frame.copy(deep=True)
        report, rows = self.report(frame)
        general = 60000 / .58
        self.assertAlmostEqual(rows["total"]["value"], (.22e6 + general) / 1e6)
        self.assertAlmostEqual(rows["total"]["monthChange"], (.06e6 + general) / 1e6)
        self.assertEqual(rows["total"]["value"], rows["total"]["knownSum"])
        self.assertAlmostEqual(rows["total"]["yearChangePct"], ((.22e6 + general) / .32e6 - 1) * 100)
        self.assertAlmostEqual(rows["mop"]["value"], (.04e6 + general - 60000) / 1e6)
        self.assertAlmostEqual(rows["mkd_total"]["value"], (.12e6 + general) / 1e6)
        self.assertAlmostEqual(rows["total"]["renovation"], (.1e6 + general) / 1e6)
        self.assertEqual(len(report["areaEstimates"]), 1)
        estimate = report["areaEstimates"][0]
        self.assertEqual((estimate["period"], estimate["year"], estimate["month"]), ("current", 2026, 2))
        self.assertEqual((estimate["sourceSheet"], estimate["sourceRow"]), ("Реестр РВ", 4602))
        self.assertIsNone(estimate["sourceGeneralArea"])
        self.assertEqual(estimate["livingArea"], 60000)
        self.assertEqual(estimate["livingShare"], .58)
        self.assertEqual(estimate["reconstructionBasis"], "object_name")
        self.assertAlmostEqual(estimate["generalArea"], general)
        self.assertAlmostEqual(estimate["mopArea"], general - 60000)
        self.assertEqual(rows["nonres_objects"]["value"], .1)
        pd.testing.assert_frame_equal(frame, before)
        self.assertEqual(report, self.report(frame)[0])

    def test_same_rule_restores_previous_year_totals_but_does_not_infer_missing_living(self):
        frame = self.rows()
        frame.loc[[0, 8], ["Общая площадь", "category_моп"]] = 0
        frame.loc[[0, 8], "Общая площадь_measured"] = False
        frame.loc[[0, 8], "Наименование объекта"] = "РЕКОНСТРУКЦИЯ жилого дома"
        report, rows = self.report(frame)
        self.assertEqual(rows["total"]["yearChangePct"], 0)
        self.assertEqual([row["period"] for row in report["areaEstimates"]], ["previous", "current"])
        # Selecting January must not include February-only estimates.
        frame.loc[4, ["Общая площадь", "category_моп"]] = 0
        frame.loc[4, "Общая площадь_measured"] = False
        january, _ = self.report(frame, month=1)
        self.assertEqual(len(january["areaEstimates"]), 2)
        frame.loc[4, "Общая площадь"] = 100000
        frame.loc[4, "Общая площадь_measured"] = True
        frame.loc[4, "Жилая площадь_measured"] = False
        _, rows = self.report(frame)
        self.assertEqual(rows["total"]["yearChangePct"], 0)
        self.assertIsNone(rows["mkd_residential"]["yearChangePct"])
        self.assertIsNone(rows["mop"]["yearChangePct"])

    def test_reconstruction_rule_preserves_supplied_totals_and_excludes_other_scope(self):
        for index, changed in (
            (8, {"Общая площадь": 0, "Общая площадь_measured": True}),
            (8, {"Общая площадь": -1, "Общая площадь_measured": False}),
            (8, {"Общая площадь": 0, "Общая площадь_measured": False, "Подтип объекта": "ИЖС"}),
            (8, {"Общая площадь": 0, "Общая площадь_measured": False, "Жилая площадь": 0}),
            (8, {"Общая площадь": 0, "Общая площадь_measured": False, "Жилая площадь_measured": False}),
            (8, {"Общая площадь": 0, "Общая площадь_measured": False, "Жилая площадь": float("inf")}),
            (8, {"Общая площадь": 0, "Общая площадь_measured": False, "Наименование объекта": "Новое строительство МКД"}),
            (9, {"Общая площадь": 0, "Общая площадь_measured": False, "Жилая площадь": 60000}),
            (10, {"Общая площадь": 0, "Общая площадь_measured": False, "Жилая площадь": 60000}),
        ):
            with self.subTest(changed=changed):
                frame = self.rows()
                frame["Жилая площадь"] = frame["Жилая площадь"].astype(float)
                frame["Наименование объекта"] = "Реконструкция здания"
                for key, value in changed.items():
                    frame.loc[index, key] = value
                report, _ = self.report(frame)
                self.assertEqual(report["areaEstimates"], [])
        report, rows = self.report()
        self.assertEqual(report["areaEstimates"], [])
        self.assertAlmostEqual(rows["total"]["value"], .32)

    def test_confirmed_reconstruction_without_word_in_k_uses_only_exact_permit(self):
        frame = self.rows()
        frame.loc[0, ["Общая площадь", "category_моп"]] = 0
        frame.loc[0, "Общая площадь_measured"] = False
        frame.loc[0, "Наименование объекта"] = "Жилой комплекс"
        frame.loc[0, "№ РВ"] = "77-01-012565-2025"
        report, rows = self.report(frame)
        self.assertIsNotNone(rows["total"]["yearChangePct"])
        self.assertEqual(report["areaEstimates"][0]["reconstructionBasis"], "confirmed_permit")
        for permit in ("77-01-012565-2026", "", None):
            frame.loc[0, "№ РВ"] = permit
            report, rows = self.report(frame)
            self.assertEqual(report["areaEstimates"], [])
            self.assertIsNone(rows["total"]["yearChangePct"])


if __name__ == "__main__":
    unittest.main()
