"""Provider category meaning, raw-cell admission and honest annual composition."""
from io import BytesIO
from pathlib import Path
import tempfile
import unittest

import pandas as pd
from openpyxl import load_workbook

from backend.annual_routes import export_workbook
from pipeline.annual_calculations import _housing_composition, _monitoring_housing_categories, _sum_range, append_chart
from pipeline.commissioning_validation import validate_emiss_frame
from pipeline.data_access import DataAccess, DataContext, _parse_emiss_34118_periods_file

TOTAL = "Жилые здания, жилые помещения в нежилых зданиях и жилые дома, построенные населением"
IHS = "Жилые дома, построенные населением"
RURAL = "Жилые дома в сельской местности, построенные населением"
MKD = "Жилые здания многоквартирные"


def source(path, categories, reverse=False):
    """Minimal independently specified cells from the provider's real taxonomy."""
    rows = [[None, None, 2025, 2026, 2026, 2026],
            [None, None, "январь-декабрь", "I квартал", "январь-июль", "январь-август"]]
    items = list(categories.items())[::-1] if reverse else list(categories.items())
    for region in ("Российская Федерация", "Город Москва"):
        rows += [[region, label, *values] for label, values in items]
    with pd.ExcelWriter(path) as writer:
        pd.DataFrame(rows).to_excel(writer, sheet_name="Данные", index=False, header=False)


class CommissioningCategoriesTests(unittest.TestCase):
    def setUp(self):
        self.directory = Path(self.enterContext(tempfile.TemporaryDirectory()))

    def test_total_ihs_rural_and_explicit_mkd_are_independent_in_either_row_order(self):
        categories = {TOTAL: [7393.9, 1325, 3324.4, 3722.2], IHS: [601.5, 113.6, 171.3, 214.2],
                      RURAL: [50, 20, 30, 40], MKD: [6792.5, None, None, None],
                      "Жилые здания": [7200, 1300, 3300, 3700]}
        for reverse in (False, True):
            path = self.directory / f"source{reverse}.xlsx"
            source(path, categories, reverse)
            frame = _parse_emiss_34118_periods_file(path)
            validate_emiss_frame(path, frame)
            selected = frame.loc[frame.region.eq("Москва")].set_index(["year", "month"])
            self.assertAlmostEqual(selected.loc[(2025, 12), "ИЖС"], .6015)
            self.assertAlmostEqual(selected.loc[(2025, 12), "TOTAL"], 7.3939)
            self.assertAlmostEqual(selected.loc[(2025, 12), "МКД"], 6.7925)
            for month, total, ihs in ((3, 1.325, .1136), (7, 3.3244, .1713), (8, 3.7222, .2142)):
                row = selected.loc[(2026, month)]
                self.assertAlmostEqual(row.TOTAL, total)
                self.assertAlmostEqual(row["ИЖС"], ihs)
                self.assertTrue(pd.isna(row["МКД"]))
            self.assertEqual(selected.loc[(2025, 12), "ИЖС_source_label"], IHS)

    def test_new_total_export_does_not_erase_older_explicit_mkd_provenance(self):
        base = self.directory / "data/raw/realty/vvod"
        base.mkdir(parents=True)
        # The loader's static legacy extension also accepts an XLSX container.
        temporary = base / "base.xlsx"
        source(temporary, {MKD: [6792.5, None, None, None], IHS: [600, None, None, None]})
        temporary.rename(base / "emiss_34118_base.xls")
        downloads = self.directory / "downloads"
        downloads.mkdir()
        newer = downloads / "Введено в действие общей площади жилых домов_new.xlsx"
        source(newer, {TOTAL: [7393.9, 1325, 3324.4, 3722.2], IHS: [601.5, 113.6, 171.3, 214.2]})
        access = DataAccess(DataContext(self.directory, downloads=downloads, use_marts=False))
        row = access.load_emiss_34118_periods().query("region == 'Москва' and year == 2025").iloc[0]
        self.assertAlmostEqual(row["МКД"], 6.7925)
        self.assertEqual(row["МКД_source_file"], "emiss_34118_base.xls")
        self.assertEqual(row["ИЖС_source_file"], newer.name)
        self.assertTrue(pd.isna(access.load_emiss_34118_periods().query("year == 2026").iloc[0]["МКД"]))

    def test_independent_raw_oracle_rejects_misclassification_fabrication_and_provenance(self):
        path = self.directory / "source.xlsx"
        source(path, {TOTAL: [7393.9, 1325, 3324.4, 3722.2], IHS: [601.5, 113.6, 171.3, 214.2], RURAL: [50, 20, 30, 40]})
        frame = _parse_emiss_34118_periods_file(path)
        for change in ("TOTAL_as_IHS", "rural_as_IHS", "residual_as_MKD", "provenance"):
            bad = frame.copy()
            if change == "TOTAL_as_IHS": bad["ИЖС"] = bad.TOTAL
            elif change == "rural_as_IHS": bad["ИЖС"] = bad["ИЖС_сельское"]
            elif change == "residual_as_MKD": bad["МКД"] = bad.TOTAL - bad["ИЖС"]
            else: bad["ИЖС_source_row"] += 1
            with self.subTest(change=change), self.assertRaises(ValueError):
                validate_emiss_frame(path, bad)
        raw = pd.read_excel(path, sheet_name="Данные", header=None)
        raw = pd.concat([raw, raw.iloc[[2]]], ignore_index=True)
        with pd.ExcelWriter(path) as writer:
            raw.to_excel(writer, sheet_name="Данные", header=False, index=False)
        with self.assertRaisesRegex(ValueError, "duplicate"):
            _parse_emiss_34118_periods_file(path)

    def test_residual_is_unclassified_and_unknown_or_hidden_ihs_never_becomes_zero(self):
        frame = pd.DataFrame([
            {"year": 2011, "TOTAL": 3, "МКД": None, "ИЖС": None},
            {"year": 2014, "TOTAL": 3, "МКД": 2, "ИЖС": 1},
            {"year": 2023, "TOTAL": 3, "МКД": None, "ИЖС": .5},
            {"year": 2025, "TOTAL": 7.3939, "МКД": 6.7925, "ИЖС": .6015},
            {"year": 2026, "TOTAL": 4.2696884, "МКД": 4.0554834, "ИЖС": .214205},
        ])
        shown = _housing_composition(frame).set_index("year")
        self.assertEqual(shown.loc[2011, "Остальное_жильё"], 3)
        self.assertTrue(pd.isna(shown.loc[2014, "ИЖС"]))
        self.assertTrue(pd.isna(shown.loc[2014, "Остальное_жильё"]))
        self.assertTrue(pd.isna(shown.loc[2023, "МКД"]))
        self.assertEqual(shown.loc[2023, "Остальное_жильё"], 2.5)
        self.assertTrue(shown.loc[[2025, 2026], "Остальное_жильё"].isna().all())
        self.assertEqual(frame.loc[1, "ИЖС"], 1)  # Source data remains unchanged.

    def test_verified_rv_subtypes_and_missing_living_cells_remain_independent(self):
        rv = pd.DataFrame([{ "Год ввода по Мосстату": 2026, "Отрасли": "Жилые объекты", "Группировка": "Жилье",
                             "Подтип объекта": subtype, "category_жилое": value, "source_row": index,
                             "Жилая площадь_measured": measured} for subtype, value, index, measured in
                           (("МКД", 1211433.2, 2, True), ("ИЖС", 113573, 3, True))])
        row = _monitoring_housing_categories(rv, 2026)
        self.assertAlmostEqual(row["TOTAL"], 1.3250062)
        self.assertAlmostEqual(row["МКД"], 1.2114332)
        self.assertAlmostEqual(row["ИЖС"], .113573)
        rv.loc[1, "Жилая площадь_measured"] = False
        row = _monitoring_housing_categories(rv, 2026)
        self.assertTrue(pd.isna(row["ИЖС"]))
        self.assertTrue(pd.isna(row["TOTAL"]))
        self.assertAlmostEqual(row["МКД"], 1.2114332)

    def test_table_export_preserves_control_unknowns_provenance_and_incomplete_sums(self):
        frame = pd.DataFrame([{"year": 2025, "TOTAL": 7.3939, "МКД": 6.7925, "ИЖС": .6015,
                               "МКД_source_file": "explicit-base.xls", "ИЖС_source_file": "dynamic.xls"},
                              {"year": 2026, "TOTAL": 1.325, "МКД": None, "ИЖС": .1136}])
        charts = []
        append_chart(charts, frame, [("МКД", "МКД", "green"), ("ИЖС", "ИЖС", "amber")], year_from=2025, year_to=2026, key="b2_1")
        chart = charts[0]
        self.assertEqual(chart["controlTotal"], "TOTAL")
        self.assertNotIn("TOTAL", [series["id"] for series in chart["series"]])
        self.assertIsNone(chart["rows"][1]["МКД"])
        self.assertIsNone(_sum_range(frame, "МКД", 2025, 2026))
        self.assertIsNone(_sum_range(frame, "ИЖС", 2024, 2026))
        book = load_workbook(BytesIO(export_workbook(dict(version="test", generatedAt="test", region={}, source={}, notes=[], charts=charts))))
        self.assertIsNone(book["b2_1"]["B3"].value)
        self.assertAlmostEqual(book["b2_1"]["D2"].value, 7.3939)
        self.assertIn("explicit-base.xls", book["Report"]["B7"].value)


if __name__ == "__main__":
    unittest.main()
