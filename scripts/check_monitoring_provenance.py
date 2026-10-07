"""Source-row identity prevents loss of repeated UIN monitoring components."""
from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True

from pipeline.data_access import DataAccess, DataContext
from pipeline.orchestrator import _deduplicate_processed
from pipeline.parsers import nashdom_monitoring_2_0 as parser
from pipeline.source_records import file_sha256, source_record_id


class MonitoringProvenanceChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.path = self.root / "monitoring_2_0_20260717.xlsx"
        # Actual component values observed in List4, rows 1284–1296, same UIN.
        values = [17055.4, 825.1, 1262.3, 17567.3, 1458.4, 2509.8, 4646.7,
                  14256.9, 809.6, 459.5, 25421.3, 1288.8, 1520.8]
        old = pd.DataFrame({"УИН": ["HG8891-10-0002-001"] * len(values),
                            "Группа компаний": ["Компания"] * len(values),
                            "Год ввода по Мосстату": [2017] * len(values),
                            "Общая площадь": values, "Жилая площадь": [0] * len(values),
                            "Отрасли": ["Жилые объекты"] * len(values),
                            "Группировка": ["Жилье"] * len(values)})
        rv = old.iloc[:2].copy()
        rv["УИН"] = "HG6561-16-0001-003"
        rv["№ РВ"] = ["77-0-011606-2023", "77-02-012398-2024"]
        rv["Год ввода по Мосстату"] = [2023, 2024]
        rv["Общая площадь"] = [15251.7, 4819.9]
        # Identical values and a missing UIN still represent distinct source rows.
        oks = pd.concat([rv.iloc[[0]]] * 4, ignore_index=True).assign(Назначение="Жилье")
        oks["Общая площадь"] = 54716.2
        oks.loc[2:, "УИН"] = None
        with pd.ExcelWriter(self.path) as workbook:
            rv.to_excel(workbook, sheet_name="Реестр РВ", index=False)
            oks.to_excel(workbook, sheet_name="Реестр ОКС", index=False)
            old.to_excel(workbook, sheet_name="Лист4", index=False)

    def test_same_uin_components_are_preserved_and_reimport_is_idempotent(self):
        parsed = parser.parse(self.path)
        deduped = _deduplicate_processed(parsed)
        self.assertEqual(len(parsed), len(deduped))
        component = deduped[(deduped["source_sheet"] == "Лист4") & (deduped["view"] == "total_area_m2")]
        self.assertEqual(len(component), 13)
        self.assertAlmostEqual(component["value"].sum(), 89081.9)
        again = parsed.copy()
        again["loaded_at"] = "another retrieval"
        self.assertEqual(len(_deduplicate_processed(pd.concat([parsed, again], ignore_index=True))), len(parsed))

    def test_equal_values_and_missing_uin_remain_separate_records(self):
        deduped = _deduplicate_processed(parser.parse(self.path))
        oks = deduped[(deduped["registry"] == "oks") & (deduped["view"] == "total_area_m2")]
        self.assertEqual(len(oks), 4)
        self.assertEqual(oks["source_row"].tolist(), [2, 3, 4, 5])
        self.assertEqual(oks["source_record_id"].nunique(), 4)
        self.assertEqual(oks["uin"].isna().sum(), 2)

    def test_core_and_metric_parser_share_exact_excel_row_identity(self):
        parsed = parser.parse(self.path)
        wide = DataAccess(DataContext(self.root, use_marts=False)).load_monitoring_2_0(
            source_path=self.path, include_provenance=True)
        digest = file_sha256(self.path)
        for frame in (wide["rv"], wide["oks"]):
            for _, row in frame.iterrows():
                expected = source_record_id(digest, row["source_sheet"], row["source_row"])
                self.assertEqual(row["source_record_id"], expected)
                metric_rows = parsed[parsed["source_record_id"] == expected]
                self.assertFalse(metric_rows.empty)
                self.assertEqual(set(metric_rows["source_file_sha256"]), {digest})

    def test_conflicting_same_source_metric_fails_instead_of_keep_last(self):
        parsed = parser.parse(self.path)
        conflict = parsed.iloc[[0]].copy()
        conflict["value"] += 1
        with self.assertRaisesRegex(ValueError, "Conflicting values"):
            _deduplicate_processed(pd.concat([parsed, conflict], ignore_index=True))

    def test_legacy_without_provenance_cannot_be_safely_collapsed(self):
        legacy = parser.parse(self.path).drop(columns=["source_record_id", "source_row", "source_file_sha256"])
        self.assertEqual(len(_deduplicate_processed(legacy)), len(legacy))

    def test_mixed_legacy_does_not_disable_modern_conflict_detection(self):
        parsed = parser.parse(self.path).iloc[[0]].copy()
        conflict = parsed.copy()
        conflict["value"] += 1
        for missing in (None, ""):
            legacy = parsed.copy()
            legacy["source_record_id"] = missing
            with self.subTest(legacy_id=missing):
                mixed = pd.concat([parsed, conflict, legacy], ignore_index=True)
                with self.assertRaisesRegex(ValueError, "Conflicting values"):
                    _deduplicate_processed(mixed)

    def test_mixed_legacy_is_preserved_while_modern_repeat_is_idempotent(self):
        parsed = parser.parse(self.path).iloc[[0]].copy()
        legacy = parsed.copy()
        legacy["source_record_id"] = None
        mixed = pd.concat([parsed, parsed, legacy, legacy], ignore_index=True)
        result = _deduplicate_processed(mixed)
        self.assertEqual(len(result), 3)
        self.assertEqual(result["source_record_id"].isna().sum(), 2)


if __name__ == "__main__":
    unittest.main(verbosity=2)
