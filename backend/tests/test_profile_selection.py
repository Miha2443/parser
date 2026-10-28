import unittest
from unittest.mock import patch

import pandas as pd

from pipeline.profile_calculations import active_construction, construction_developers, make_profile, renovation_annual, renovation_donut
from pipeline.renovation import RENOVATION_VALUES, renovation_living_by_year, renovation_rows


class ProfileSelectionTests(unittest.TestCase):
    def test_catalog_uses_only_active_moscow_construction_and_descending_area(self):
        rows = pd.DataFrame([
            {"Группа компаний": "Альфа", "Общая площадь": 200, "Статус РС": "Действует", "Ввод в эксплуатацию": "В строительстве"},
            {"Группа компаний": "Бета", "Общая площадь": 450, "Статус РС": "Действует", "Ввод в эксплуатацию": "В строительстве"},
            {"Группа компаний": "Бета", "Общая площадь": 50, "Статус РС": "Действует", "Ввод в эксплуатацию": "В строительстве"},
            {"Группа компаний": "Альфа", "Общая площадь": 9999, "Статус РС": "Не действует", "Ввод в эксплуатацию": "В строительстве"},
            {"Группа компаний": "Только ввод", "Общая площадь": 99999, "Статус РС": "Действует", "Ввод в эксплуатацию": "Объект введен"},
        ])
        before = rows.copy(deep=True)
        result = construction_developers({"oks": rows, "developers": ["Только РВ"]})
        self.assertEqual([row["developer"] for row in result], ["Бета", "Альфа"])
        self.assertEqual([row["constructionAreaM2"] for row in result], [500, 200])
        self.assertEqual([row["constructionObjectCount"] for row in result], [2, 1])
        pd.testing.assert_frame_equal(rows, before)

    def test_missing_status_never_exposes_historical_objects_as_active(self):
        rows = pd.DataFrame([{"Группа компаний": "Альфа", "Общая площадь": 100}])
        self.assertTrue(active_construction(rows)[0].empty)
        self.assertEqual(construction_developers({"oks": rows}), [])
        rows["Статус объекта"] = " Строящийся "
        self.assertEqual(len(construction_developers({"oks": rows})), 1)
        rows["Статус РС"] = "Не действует"
        rows["Ввод в эксплуатацию"] = "В строительстве"
        self.assertEqual(construction_developers({"oks": rows}), [])

    def test_unknown_area_is_not_zero_and_cannot_outrank_measured_construction(self):
        rows = pd.DataFrame([
            {"Группа компаний": name, "Общая площадь": value, "Статус РС": "Действует", "Ввод в эксплуатацию": "В строительстве"}
            for name, value in [("Без площади", None), ("Ноль", 0), ("Бесконечность", float("inf"))]
        ])
        result = construction_developers({"oks": rows})
        self.assertEqual(result[0]["developer"], "Ноль")
        self.assertEqual([row["constructionAreaM2"] for row in result], [0, None, None])

    def test_renovation_profile_and_annual_chart_use_identical_living_scope(self):
        rv = pd.DataFrame([
            {"Группа компаний": company, "Инвестор для Реновации": flag, "Год ввода по Мосстату": year,
             "Жилая площадь": area, "category_жилое": area, "category_моп": 10,
             "category_нежилое_в_жилом": 0, "category_нежилое_отдельное": 0}
            for company, flag, year, area in [("Другая компания", " да ", 2025, 100), ("ФОНД РЕНОВАЦИИ", "ДА", 2025, 200),
                                            ("ФОНД РЕНОВАЦИИ", "нет", 2025, 9000), ("Другая компания", "да", 2026, 50)]
        ])
        before = rv.copy(deep=True)
        selected = renovation_rows(rv)
        self.assertEqual(len(selected), 3)
        profile = renovation_annual(selected, 2026)
        annual = renovation_living_by_year(rv, 2026)
        actual = {row["year"]: row["valuesM2"]["residential"] / 1_000_000 for row in profile["allYears"]}
        self.assertEqual(actual, dict(zip(annual["year"], annual["Реновация"])))
        self.assertEqual(actual[2017], RENOVATION_VALUES[2017])
        self.assertAlmostEqual(actual[2025], .0003)
        current = renovation_donut("latest", "Latest", profile, [2026])
        self.assertEqual(current["segments"][0]["valueM2"], 50)
        historical = renovation_donut("old", "Old", profile, [2017])
        self.assertEqual(historical["segments"][0]["valueM2"], 300000)
        self.assertIsNone(historical["segments"][1]["valueM2"])
        self.assertIsNone(historical["totalM2"])
        self.assertEqual(historical["status"], "partial")
        pd.testing.assert_frame_equal(rv, before)

    def test_program_scope_does_not_change_company_specific_erz_denominators(self):
        rv = pd.DataFrame([{"Группа компаний": company, "Инвестор для Реновации": "да", "Год ввода по Мосстату": 2026,
                            "Жилая площадь": value, "category_жилое": value, "category_моп": 0,
                            "category_нежилое_в_жилом": 0, "category_нежилое_отдельное": 0}
                           for company, value in [("ФОНД РЕНОВАЦИИ", 100), ("Другой инвестор", 900)]])
        data = {"monitoring_2_0": {"rv": rv, "oks": pd.DataFrame(), "max_year": 2026},
                "erzrf_cards": pd.DataFrame(), "erzrf_top": {}, "rasprodannost": {}, "kvartirografia": {}, "escrow_manual": pd.DataFrame()}
        with patch('pipeline.profile_calculations.delay_data', return_value={}) as delay, \
             patch('pipeline.profile_calculations.housing_data', return_value=[]) as housing, \
             patch('pipeline.profile_calculations.object_tables', return_value={}), \
             patch('pipeline.profile_calculations.ratings_data', return_value={}), \
             patch('pipeline.profile_calculations.rasprod_data', return_value={}), \
             patch('pipeline.profile_calculations.apartment_data', return_value={}), \
             patch('pipeline.profile_calculations.escrow_data', return_value={}):
            result = make_profile("ФОНД РЕНОВАЦИИ", "msk", data)
        self.assertEqual(delay.call_args.args[2]["Жилая площадь"].sum(), 100)
        self.assertEqual(housing.call_args.args[0]["Жилая площадь"].sum(), 100)
        self.assertEqual(result["annual"]["allYears"][-1]["valuesM2"]["residential"], 1000)
        self.assertEqual(result["controls"]["commissioningScope"], "renovation_program")


if __name__ == "__main__":
    unittest.main()
