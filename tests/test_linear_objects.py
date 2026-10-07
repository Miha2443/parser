"""Проверки квартального плана/факта линейных объектов."""
from __future__ import annotations

import unittest
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.linear_objects import SOURCE_DIR, latest_source, period_summary, read_linear_objects, source_date


SOURCE = SOURCE_DIR / "linear_objects_2026-09-28.json"


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


if __name__ == "__main__":
    unittest.main()
