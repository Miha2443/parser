import pandas as pd
import unittest
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.vvod_operational import housing_ytd_table, nonres_ytd_table, quarter_tree


def _rv() -> pd.DataFrame:
    return pd.DataFrame([
        {
            "Год ввода по Мосстату": 2026, "Месяц ввода по Мосстату": "Январь",
            "Квартал ввода по Мосстату": "1-й квартал", "Отрасли": "Жилые объекты",
            "Подтип объекта": "МКД", "Общая площадь": 170.0, "category_жилое": 100.0,
            "category_моп": 50.0, "category_нежилое_в_жилом": 20.0,
            "category_нежилое_отдельное": 0.0,
        },
        {
            "Год ввода по Мосстату": 2026, "Месяц ввода по Мосстату": "Февраль",
            "Квартал ввода по Мосстату": "1-й квартал", "Отрасли": "Жилые объекты",
            "Подтип объекта": "ИЖС", "Общая площадь": 30.0, "category_жилое": 30.0,
            "category_моп": 0.0, "category_нежилое_в_жилом": 0.0,
            "category_нежилое_отдельное": 0.0,
        },
        {
            "Год ввода по Мосстату": 2026, "Месяц ввода по Мосстату": "Март",
            "Квартал ввода по Мосстату": "1-й квартал", "Отрасли": "Административно-деловые объекты",
            "Подтип объекта": "МПТ", "Общая площадь": 40.0, "category_жилое": 0.0,
            "category_моп": 0.0, "category_нежилое_в_жилом": 0.0,
            "category_нежилое_отдельное": 40.0,
        },
    ])


class OperationalVvodChecks(unittest.TestCase):
    def test_housing_uses_historical_months_and_current_registry(self):
        annual = pd.DataFrame({"year": [2025], "жильё": [1.0]})
        history = pd.DataFrame({"year": [2025, 2025], "month": [1, 2], "value_thousand_m2": [200, 300]})
        result = housing_ytd_table(annual, _rv(), history, 2).set_index("Год")
        self.assertAlmostEqual(result.loc[2025, "За выбранный период, млн м²"], .5)
        self.assertAlmostEqual(result.loc[2026, "За выбранный период, млн м²"], .00013)


    def test_nonres_total_includes_housing_premises_but_excluded_mode_does_not(self):
        annual = pd.DataFrame({"year": [2026], "нежильё": [.04], "общая": [.06]})
        total = nonres_ytd_table(annual, _rv(), 3, exclude_mkd=False).set_index("Год")
        excluded = nonres_ytd_table(annual, _rv(), 3, exclude_mkd=True).set_index("Год")
        self.assertAlmostEqual(total.loc[2026, "За выбранный период, млн м²"], .00006)
        self.assertAlmostEqual(excluded.loc[2026, "За выбранный период, млн м²"], .00004)


    def test_quarter_tree_balances_total_and_branches(self):
        tree = quarter_tree(_rv(), 2026, 1)
        self.assertAlmostEqual(tree["total"], .00024)
        self.assertAlmostEqual(tree["housing_objects"] + tree["nonres_objects"], tree["total"])
        self.assertAlmostEqual(tree["mkd_total"], .00017)
        self.assertAlmostEqual(tree["nonres_total"], .00006)


if __name__ == "__main__":
    unittest.main()
