"""Проверки квартального плана/факта линейных объектов."""
from __future__ import annotations

import unittest
import sys
import shutil
import math
from io import BytesIO
from pathlib import Path
from tempfile import TemporaryDirectory
from xml.etree import ElementTree
from zipfile import ZipFile

from openpyxl import Workbook, load_workbook

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.linear_objects import (SOURCE_DIR, latest_source, period_summary, read_linear_objects,
                                read_linear_sheet, source_date)
from tests.monitoring_dsti_fixture import add_monitoring_dsti


SOURCE = SOURCE_DIR / "linear_objects_2026-09-28.json"
NOVEMBER_SOURCE = SOURCE_DIR / "linear_objects_2026-11-05.json"


class LinearObjectsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = read_linear_objects(SOURCE)

    def test_source_rows_and_periods(self) -> None:
        self.assertEqual(set(self.data["code"]), {"1.1", "1.2", "1.3", "1.4"})
        self.assertEqual(len(self.data), 16)
        self.assertEqual(source_date(SOURCE).isoformat(), "2026-09-28")

    def test_quarter_and_year_to_date_are_distinct(self) -> None:
        quarter = period_summary(self.data, 2026, 3, False).set_index("code")
        ytd = period_summary(self.data, 2026, 3, True).set_index("code")
        self.assertAlmostEqual(quarter.loc["1.1", "plan"], 21.6)
        self.assertAlmostEqual(quarter.loc["1.1", "fact"], 16.1)
        self.assertAlmostEqual(ytd.loc["1.1", "plan"], 40.6)
        self.assertAlmostEqual(ytd.loc["1.1", "fact"], 37.1)
        self.assertAlmostEqual(ytd.loc["1.1", "percent"], 37.1 / 40.6 * 100)

    def test_missing_fourth_quarter_fact_stays_missing(self) -> None:
        quarter = period_summary(self.data, 2026, 4, False)
        ytd = period_summary(self.data, 2026, 4, True)
        self.assertTrue(quarter["fact"].isna().all())
        self.assertTrue(ytd["fact"].isna().all())
        self.assertTrue(ytd["percent"].isna().all())
        self.assertAlmostEqual(ytd.set_index("code").loc["1.1", "plan"], 85.3)

    def test_overfulfillment_is_not_capped(self) -> None:
        quarter = period_summary(self.data, 2026, 1, False).set_index("code")
        self.assertAlmostEqual(quarter.loc["1.1", "percent"], 200.0)

    def test_new_excel_replaces_older_snapshot(self) -> None:
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            (folder / "linear_objects_2026-09-28.json").write_text("[]", encoding="utf-8")
            newer = folder / "Финансовая дисциплина ДСТИ 07.10.2026.xlsx"
            newer.write_bytes(b"")
            self.assertEqual(latest_source(folder), newer)


class NovemberLinearObjectsTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.data = read_linear_objects(NOVEMBER_SOURCE)

    def test_snapshot_matches_dsti_rows_5_to_8_quarter_columns(self) -> None:
        # ДСТИ 05.11.2026, «ДСТИ с учетом переносов», F:Q rows 5:8.
        expected = {
            "1.1": ([5.1, 13.9, 21.6, 34.0], [10.2, 10.8, 24.6, 1.5]),
            "1.2": ([4.0, 6.0, 7.0, 10.0], [4.0, 3.0, 9.0, None]),
            "1.3": ([2.0, 5.0, 8.0, 19.0], [5.0, 1.0, 3.0, 3.0]),
            "1.4": ([5.0, 5.0, 9.0, 9.0], [3.0, 7.0, 5.0, None]),
        }
        self.assertEqual(len(self.data), 16)
        self.assertEqual(set(self.data["code"]), set(expected))
        self.assertEqual(set(self.data["year"]), {2026})
        self.assertEqual(source_date(NOVEMBER_SOURCE).isoformat(), "2026-11-05")
        for code, (plans, facts) in expected.items():
            group = self.data[self.data["code"].eq(code)]
            self.assertEqual(group["quarter"].tolist(), [1, 2, 3, 4])
            self.assertEqual(group["plan"].tolist(), plans)
            actual = group["fact"].astype(object).where(group["fact"].notna(), None).tolist()
            self.assertEqual(actual, facts)

    def test_partial_fourth_quarter_and_missing_values_are_not_annual_zeros(self) -> None:
        quarter = period_summary(self.data, 2026, 4, False).set_index("code")
        ytd = period_summary(self.data, 2026, 4, True).set_index("code")
        self.assertAlmostEqual(quarter.loc["1.1", "fact"], 1.5)
        self.assertAlmostEqual(ytd.loc["1.1", "fact"], 47.1)
        self.assertEqual(ytd.loc["1.3", "fact"], 12)
        self.assertTrue(quarter.loc[["1.2", "1.4"], "fact"].isna().all())
        self.assertTrue(ytd.loc[["1.2", "1.4"], "fact"].isna().all())
        self.assertTrue(ytd.loc[["1.2", "1.4"], "percent"].isna().all())

    def test_new_snapshot_wins_by_report_date_but_not_over_newer_operator_excel(self) -> None:
        with TemporaryDirectory() as directory:
            folder = Path(directory)
            for path in (SOURCE, NOVEMBER_SOURCE):
                shutil.copy2(path, folder / path.name)
            self.assertEqual(latest_source(folder).name, NOVEMBER_SOURCE.name)
            newer = folder / "Финансовая дисциплина ДСТИ 06.11.2026.xlsx"
            newer.write_bytes(b"")
            self.assertEqual(latest_source(folder), newer)


class MonitoringDstiParserTests(unittest.TestCase):
    def setUp(self):
        self.book = Workbook()
        self.addCleanup(self.book.close)
        self.tab = add_monitoring_dsti(self.book)

    def test_date_codes_and_cached_quarters_use_only_commissioning_rows(self):
        self.tab.append(["1.1", "Контрактация работ", "млн руб.", "на конец периода", None, 999999])
        data = read_linear_sheet(self.tab, monitoring=True)
        self.assertEqual(len(data), 16)
        self.assertEqual(set(data["code"]), {"1.1", "1.2", "1.3", "1.4"})
        roads = data[data["code"].eq("1.1")]
        self.assertEqual(roads["fact"].tolist(), [10.2, 10.8, 24.6, 1.5])
        self.assertEqual(roads["fact_cell"].tolist(), ["F5", "I5", "L5", "O5"])
        self.assertEqual(roads["plan_cell"].tolist(), ["G5", "J5", "M5", "P5"])
        self.assertAlmostEqual(roads["plan"].sum(), 74.6)
        ytd = period_summary(data, 2026, 4, True).set_index("code")
        self.assertAlmostEqual(ytd.loc["1.1", "fact"], 47.1)
        self.assertEqual(ytd.loc["1.3", "fact"], 12)
        self.assertTrue(ytd.loc[["1.2", "1.4"], "fact"].isna().all())

    def test_named_sheet_wins_over_active_sheet(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "monitoring_2_0_20261110.xlsx"
            self.book.save(path)
            self.assertEqual(len(read_linear_objects(path)), 16)

    def test_legacy_active_sheet_with_text_codes_still_reads_quarters(self):
        self.tab.title = "ДСТИ с учетом переносов"
        self.book.active = self.book.index(self.tab)
        for row in range(5, 9):
            self.tab.cell(row, 1, f"1.{row - 4}")
        with TemporaryDirectory() as directory:
            path = Path(directory) / "legacy.xlsx"
            self.book.save(path)
            data = read_linear_objects(path)
        self.assertEqual(len(data), 16)
        self.assertEqual(data.iloc[0]["fact"], 10.2)

    def test_formula_without_cached_value_stays_missing(self):
        self.tab["O6"] = "=1+2"
        stream = BytesIO()
        self.book.save(stream)
        stream.seek(0)
        book = load_workbook(stream, read_only=True, data_only=True)
        try:
            data = read_linear_sheet(book["ДСТИ"], monitoring=True)
            self.assertTrue(math.isnan(data[data["code"].eq("1.2")].iloc[-1]["fact"]))
        finally:
            book.close()

    def test_no_dimensions_in_source_xml_is_supported(self):
        original, reduced = BytesIO(), BytesIO()
        self.book.save(original)
        with ZipFile(original) as source, ZipFile(reduced, "w") as target:
            for entry in source.infolist():
                content = source.read(entry.filename)
                if entry.filename == "xl/worksheets/sheet2.xml":
                    xml = ElementTree.fromstring(content)
                    xml.remove(xml.find("{http://schemas.openxmlformats.org/spreadsheetml/2006/main}dimension"))
                    content = ElementTree.tostring(xml)
                target.writestr(entry, content)
        reduced.seek(0)
        book = load_workbook(reduced, read_only=True, data_only=True)
        try:
            self.assertIsNone(book["ДСТИ"].max_row)
            self.assertEqual(len(read_linear_sheet(book["ДСТИ"], monitoring=True)), 16)
        finally:
            book.close()

    def test_rows_and_period_columns_are_discovered_not_fixed(self):
        self.tab.move_range("A4:Q8", rows=2, cols=2)
        data = read_linear_sheet(self.tab, monitoring=True)
        self.assertEqual(data[data["code"].eq("1.1")]["fact_cell"].tolist(), ["H7", "K7", "N7", "Q7"])

    def test_duplicate_headers_and_rows_units_and_nonadditive_periods_rejected(self):
        for cell, value in (("R4", "1 квартал 2026 (факт)"), ("B9", "Протяженность дорог"),
                            ("C5", "млн руб."), ("D5", "на конец периода")):
            with self.subTest(cell=cell):
                old = self.tab[cell].value
                self.tab[cell] = value
                with self.assertRaises(ValueError):
                    read_linear_sheet(self.tab, monitoring=True)
                self.tab[cell] = old

    def test_missing_row_is_not_inferred_from_date_code(self):
        self.tab["B5"] = "Неизвестный показатель"
        data = read_linear_sheet(self.tab, monitoring=True)
        self.assertNotIn("1.1", set(data["code"]))

    def test_blank_zero_and_nonfinite_values_remain_distinct(self):
        for value in (None, "*", "nan", "inf", True, -1):
            with self.subTest(value=value):
                self.tab["O5"] = value
                data = read_linear_sheet(self.tab, monitoring=True)
                self.assertTrue(math.isnan(data[data["code"].eq("1.1")].iloc[-1]["fact"]))
        self.tab["O5"] = 0
        data = read_linear_sheet(self.tab, monitoring=True)
        self.assertEqual(data[data["code"].eq("1.1")].iloc[-1]["fact"], 0)


if __name__ == "__main__":
    unittest.main()
