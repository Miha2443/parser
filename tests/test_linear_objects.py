"""Проверки квартального плана/факта линейных объектов."""
from __future__ import annotations

import unittest
import sys
import shutil
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.linear_objects import SOURCE_DIR, latest_source, period_summary, read_linear_objects, source_date


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


if __name__ == "__main__":
    unittest.main()
