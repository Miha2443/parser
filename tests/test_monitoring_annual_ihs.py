"""Annual IHS admission uses explicit S cells, not nearby totals/months."""
import hashlib
import os
from pathlib import Path
import tempfile
import unittest

import pandas as pd

from pipeline.data_access import DataAccess, DataContext, MONTH_NAMES_RU
from pipeline.monitoring_annual import IHS_LABEL, SHEET, read_annual_ihs


ROOT = Path(__file__).resolve().parents[1]
REAL_SOURCE = ROOT / "data/raw/realty/nashdom/monitoring_2_0_20261016.xlsx"


def source_frame():
    frame = pd.DataFrame([[None] * 19 for _ in range(63)])
    for title_row, label, annual in ((1, "Ввод жилья", 2200),
                                      (21, IHS_LABEL, 25.8),
                                      (41, "Ввод МКД, тыс. кв. м", 2174.2)):
        frame.iloc[title_row, 5] = label
        frame.iloc[title_row + 1, 6:19] = [*MONTH_NAMES_RU, "Год"]
        frame.iloc[title_row + 2, 5] = 2011
        frame.iloc[title_row + 2, 6:18] = [9999] * 12
        frame.iloc[title_row + 2, 18] = annual
    frame.iloc[61, 5] = "ИТОГ: выдача РС жилье"
    frame.iloc[62, 5] = 2011
    frame.iloc[62, 18] = 8888
    return frame


class MonitoringAnnualIhsTests(unittest.TestCase):
    def read(self, frame, source_date="2026-10-16"):
        return read_annual_ihs(frame, "monitoring_2_0_20261016.xlsx", source_date)

    def test_named_annual_cell_units_provenance_and_read_only_frame(self):
        frame = source_frame()
        before = frame.copy(deep=True)
        result = self.read(frame)
        self.assertEqual(len(result), 1)
        row = result.iloc[0]
        self.assertAlmostEqual(row["ИЖС"], .0258)
        self.assertEqual((row.region, row.year, row.month, row.period_type), ("Москва", 2011, 12, "year"))
        self.assertEqual(row["ИЖС_source_file"], "monitoring_2_0_20261016.xlsx")
        self.assertEqual(row["ИЖС_source_date"], "2026-10-16")
        self.assertEqual(row["ИЖС_source_sheet"], SHEET)
        self.assertEqual(row["ИЖС_source_label"], IHS_LABEL)
        self.assertEqual(row["ИЖС_source_cell"], "S24")
        self.assertEqual(row["ИЖС_source_year_cell"], "F24")
        self.assertEqual(row["ИЖС_source_row"], 24)
        self.assertEqual(row["ИЖС_source_column"], "S")
        self.assertEqual(row["ИЖС_source_unit"], "тыс. м²")
        self.assertEqual(row["ИЖС_source_value"], 25.8)
        self.assertEqual(row["ИЖС_source_precision_million"], .0001)
        pd.testing.assert_frame_equal(frame, before)

    def test_absent_bounded_invalid_or_uncached_annual_never_uses_months(self):
        for value in (None, "", "<1", ">1", "—", float("nan"), float("inf"), -1, True, "=SUM(G24:R24)"):
            frame = source_frame()
            frame.iloc[23, 18] = value
            with self.subTest(value=value):
                self.assertTrue(self.read(frame).empty)
        for value in (0, "25,8"):
            frame = source_frame()
            frame.iloc[23, 18] = value
            self.assertAlmostEqual(self.read(frame).iloc[0]["ИЖС"], float(str(value).replace(",", ".")) / 1000)
        # A literal annual measurement does not require reconstructed months.
        frame.iloc[23, 6:18] = None
        self.assertAlmostEqual(self.read(frame).iloc[0]["ИЖС"], .0258)

    def test_current_and_future_years_are_not_completed_annual_measurements(self):
        frame = source_frame()
        for index, year in enumerate((2025, 2026, 2027), 24):
            frame.iloc[index, 5] = year
            frame.iloc[index, 18] = 100
        result = self.read(frame)
        self.assertEqual(result.year.tolist(), [2011, 2025])
        self.assertTrue(self.read(frame, source_date=None).empty)

    def test_missing_exact_title_units_calendar_or_annual_header_fails_closed(self):
        for cell, value in (((21, 5), "Ввод ИЖС"), ((21, 5), "Ввод сельского ИЖС, тыс. кв. м"),
                            ((21, 5), "Ввод ИЖС, млн кв. м"), ((22, 6), "февраль"),
                            ((22, 18), "Январь-август")):
            frame = source_frame()
            frame.iloc[cell] = value
            with self.subTest(cell=cell, value=value):
                self.assertTrue(self.read(frame).empty)
        self.assertTrue(self.read(source_frame().iloc[:, :18]).empty)

    def test_duplicate_block_or_year_is_rejected(self):
        for change in ("block", "year", "fractional_year"):
            frame = source_frame()
            if change == "block":
                frame.iloc[41, 5] = IHS_LABEL
            elif change == "year":
                frame.iloc[24, 5] = 2011
            else:
                frame.iloc[24, 5] = 2012.5
            with self.subTest(change=change), self.assertRaisesRegex(ValueError, "multiple|duplicate|invalid"):
                self.read(frame)

    def test_loader_registry_freshness_mart_modes_and_no_old_file_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path = root / "data/raw/realty/nashdom/monitoring_2_0_20261016.xlsx"
            path.parent.mkdir(parents=True)
            source_frame().to_excel(path, sheet_name=SHEET, header=False, index=False)
            digest = hashlib.sha256(path.read_bytes()).hexdigest()
            for use_marts in (False, True):
                access = DataAccess(DataContext(root, use_marts=use_marts, require_marts=use_marts))
                signature = access.data_version("load_monitoring_annual_ihs")
                self.assertEqual(access.source_files("monitoring_annual_ihs"), [path])
                result = access.load_monitoring_annual_ihs()
                self.assertAlmostEqual(result.iloc[0]["ИЖС"], .0258)
                self.assertEqual(result.iloc[0]["ИЖС_source_file"], path.relative_to(root).as_posix())
                self.assertEqual(signature, access.data_version("load_monitoring_annual_ihs"))
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
            access = DataAccess(DataContext(root))
            signature = access.data_version("load_monitoring_annual_ihs")
            newer = path.with_name("monitoring_2_0_20261021.xlsx")
            invalid = source_frame()
            invalid.iloc[21, 5] = None
            invalid.to_excel(newer, sheet_name=SHEET, header=False, index=False)
            stamp = path.stat().st_mtime + 10
            os.utime(newer, (stamp, stamp))
            self.assertNotEqual(signature, access.data_version("load_monitoring_annual_ihs"))
            self.assertTrue(access.load_monitoring_annual_ihs().empty)
            self.assertTrue(access.issues)
            invalid.to_excel(newer, sheet_name="Other", header=False, index=False)
            self.assertTrue(access.load_monitoring_annual_ihs().empty)

    @unittest.skipUnless(REAL_SOURCE.exists(), "Saved monitoring source is unavailable")
    def test_saved_2011_2014_cells_and_completed_years_without_modifying_xlsx(self):
        digest = hashlib.sha256(REAL_SOURCE.read_bytes()).hexdigest()
        access = DataAccess(DataContext(ROOT, use_marts=False))
        history = access.load_monitoring_annual_ihs().set_index("year")
        self.assertEqual(history.index.tolist(), list(range(2011, 2026)))
        for year, row, value in ((2011, 24, 25.8), (2012, 25, 221.8), (2013, 26, 312.4), (2014, 27, 497.4)):
            self.assertAlmostEqual(history.loc[year, "ИЖС"], value / 1000)
            self.assertEqual(history.loc[year, "ИЖС_source_cell"], f"S{row}")
            self.assertEqual(history.loc[year, "ИЖС_source_value"], value)
        self.assertEqual(hashlib.sha256(REAL_SOURCE.read_bytes()).hexdigest(), digest)


if __name__ == "__main__":
    unittest.main()
