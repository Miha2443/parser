"""Monitoring area regressions: formatted numbers stay numeric components."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import pandas as pd
from pipeline.data_access import DataAccess, DataContext
from pipeline.parsers.nashdom_monitoring_2_0 import parse


class MonitoringAreaChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "monitoring_2_0_20260717.xlsx"
        values = ["4817\xa0", "3 158", "16\xa0312,5", "0", None, "?"]
        self.rows = pd.DataFrame({"УИН": ["same-component-family"] * 6,
                                  "Группа компаний": ["Developer"] * 6,
                                  "Год ввода по Мосстату": [2021] * 6,
                                  "Отрасли": ["Жилые объекты"] * 6,
                                  "Группировка": ["Жилье"] * 6,
                                  "Назначение": ["Жилье"] * 6,
                                  "Общая площадь": values,
                                  "Жилая площадь": ["1 000,25", "158\xa0", "12,5", "0", None, "?"]})
        with pd.ExcelWriter(self.path, engine="openpyxl") as book:
            self.rows.to_excel(book, sheet_name="Реестр РВ", index=False)
            self.rows.to_excel(book, sheet_name="Реестр ОКС", index=False)
            historical = self.rows.iloc[:3].copy()
            historical["Общая площадь"] = ["4817\xa0", "3158\xa0", "16312\xa0"]
            historical["Жилая площадь"] = ["4817\xa0", 0, 0]
            historical.to_excel(book, sheet_name="Лист4", index=False)
        self.payload = DataAccess(DataContext(self.root, use_marts=False)).load_monitoring_2_0(source_path=self.path, include_provenance=True)

    def test_space_nbsp_comma_and_zero_match_metric_parser_in_both_registries(self):
        metrics = parse(self.path)
        for key in ("rv", "oks"):
            frame = self.payload[key]
            if key == "rv":
                frame = frame[frame["source_sheet"] == "Реестр РВ"]
            self.assertEqual(frame["Общая площадь"].tolist(), [4817, 3158, 16312.5, 0, 0, 0])
            for row in frame.iloc[:4].itertuples(index=False):
                metric = metrics[(metrics["source_record_id"] == row.source_record_id) & (metrics["metric_column"] == "Общая площадь")]
                self.assertEqual(len(metric), 1)
                self.assertEqual(metric.iloc[0]["value"], frame.loc[frame["source_record_id"] == row.source_record_id, "Общая площадь"].iloc[0])
            self.assertEqual(frame["source_record_id"].nunique(), 6)

    def test_categories_use_normalized_areas_and_keep_unknown_representation(self):
        for frame in (self.payload["rv"].iloc[:6], self.payload["oks"]):
            self.assertEqual(frame["category_жилое"].tolist(), [1000.25, 158, 12.5, 0, 0, 0])
            self.assertEqual(frame["category_моп"].tolist(), [3816.75, 3000, 16300, 0, 0, 0])
            self.assertEqual(frame.iloc[-1]["Общая площадь"], 0)
            self.assertEqual(frame.iloc[-1]["Жилая площадь"], 0)
        # Raw unknown marker remains inspectable through provenance; the zero
        # above is compatibility behavior, not proof of an actual source zero.
        source = pd.read_excel(self.path, sheet_name="Реестр РВ")
        self.assertEqual(source.iloc[-1]["Общая площадь"], "?")

    def test_three_real_historical_nbsp_examples_recover_exact_totals(self):
        frame = self.payload["rv"]
        frame = frame[frame["source_sheet"] == "Лист4"]
        self.assertEqual(len(frame), 3)
        self.assertEqual(frame["Общая площадь"].sum(), 24287)
        self.assertEqual(frame["Жилая площадь"].sum(), 4817)
        self.assertEqual(frame["category_жилое"].sum(), 4817)
        self.assertEqual(frame["category_моп"].sum(), 19470)


if __name__ == "__main__":
    unittest.main(verbosity=2)
