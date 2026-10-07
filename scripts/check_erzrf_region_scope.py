"""Offline regression checks for scoped ERZRF cumulative commissioning data."""
from __future__ import annotations

import contextlib
import copy
import io
import json
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from dataclasses import replace

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

logging.disable(logging.CRITICAL)
from pipeline import data_access as core
from pipeline.data_access import DataAccess, DataContext, ERZ_NAKOPL_SCHEMA_VERSION


def commissioned(names: list[str], values: list[int]) -> pd.DataFrame:
    return pd.DataFrame({
        "Место": range(1, len(names) + 1),
        "Наименование, регион": names,
        "Введено, м²": values,
    })


class RegionScopeChecks(unittest.TestCase):
    def setUp(self) -> None:
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.base = Path(self.tmp.name)
        self.raw = self.base / "data/raw/realty/erzrf"
        self.raw.mkdir(parents=True)
        self.marts = self.base / "data/marts/realty"
        self.marts.mkdir(parents=True)
        self.da = DataAccess(DataContext(self.base, use_marts=False))

    def export(self, name: str, frame: pd.DataFrame) -> Path:
        path = self.raw / name
        frame.to_excel(path, index=False)
        return path

    def load(self) -> dict:
        return self.da.load_erzrf_top()

    def assert_unavailable(self, result: dict, region: str, reason: str) -> None:
        self.assertTrue(result["nakopl_vvod"][region].empty)
        quality = result["nakopl_vvod_quality"]
        self.assertEqual(quality["schema_version"], ERZ_NAKOPL_SCHEMA_VERSION)
        metadata = quality["regions"][region]
        self.assertEqual(metadata["source_scope"], region)
        self.assertEqual(metadata["status"], "unavailable")
        self.assertEqual(metadata["reason"], reason)

    def write_mart(self, value: dict) -> Path:
        path = self.marts / "erzrf_top.pkl"
        pd.to_pickle(value, path)
        (self.marts / "manifest.json").write_text(
            json.dumps({"marts": {"erzrf_top": {"file": str(path)}}}), encoding="utf-8",
        )
        self.da.context = replace(self.da.context, use_marts=True)
        return path

    def test_unknown_export_never_becomes_either_region(self) -> None:
        # Both historical branches: Moscow substring present, or no match.
        for names in (["ПИК, г.Москва", "Региональная компания"], ["Региональная компания"]):
            with self.subTest(names=names):
                self.export("TOP_EXCEL.xlsx", commissioned(names, [900] * len(names)))
                result = self.load()
                for region in ("rf", "msk"):
                    self.assert_unavailable(result, region, "missing_regional_export")

    def test_rf_export_does_not_fill_missing_moscow(self) -> None:
        rf = commissioned(["ГК Москва", "Компания вне Москвы"], [900, 700])
        self.export("top_nakopl_vvod_rf_20260702.xlsx", rf)
        result = self.load()
        pd.testing.assert_frame_equal(result["nakopl_vvod"]["rf"], rf)
        self.assert_unavailable(result, "msk", "missing_regional_export")

    def test_moscow_scope_is_source_not_developer_name(self) -> None:
        rf = commissioned(["ГК Москва"], [900])
        msk = commissioned(["Компания без географического имени"], [125])
        self.export("top_nakopl_vvod_rf_20260702.xlsx", rf)
        self.export("top_nakopl_vvod_msk_20260702.xlsx", msk)
        result = self.load()
        pd.testing.assert_frame_equal(result["nakopl_vvod"]["msk"], msk)
        self.assertEqual(result["nakopl_vvod_quality"]["regions"]["msk"]["status"], "available")
        self.assertIn("Компания без географического имени", result["all_developers"])

    def test_wrong_rating_rejected_despite_regional_filename(self) -> None:
        for region in ("rf", "msk"):
            self.export(f"top_nakopl_vvod_{region}_20260702.xlsx", pd.DataFrame({
                "Наименование, регион": ["ГК Москва"], "Строится, м²": [900], "Средняя оценка": [80],
            }))
        self.export("TOP_EXCEL.xlsx", commissioned(["ГК Москва"], [900]))
        result = self.load()
        for region in ("rf", "msk"):
            self.assert_unavailable(result, region, "invalid_commissioned_table")

    def test_unreadable_export_has_explicit_reason(self) -> None:
        (self.raw / "top_nakopl_vvod_msk_20260702.xlsx").write_bytes(b"not an Excel workbook")
        self.assert_unavailable(self.load(), "msk", "unreadable_regional_export")

    def test_real_quality_metric_is_not_commissioned_floor_area(self) -> None:
        # Column observed in the actual potreb_kachestva/rf mart: a count of
        # buildings over three years, not commissioned square metres.
        quality = pd.DataFrame({"Наименование, регион": ["Компания"],
                                "Введено МКД по ДДУ за 3 года": [21], "Средняя оценка": [90]})
        self.assertFalse(core._erz_nakopl_valid(quality))
        self.export("top_nakopl_vvod_msk_20260702.xlsx", quality)
        self.assert_unavailable(self.load(), "msk", "invalid_commissioned_table")

    def test_missing_invalid_or_negative_area_is_unavailable(self) -> None:
        for values in (["—", "—"], [125, None], [125, "wrong"], [125, -1], [125, float("inf")]):
            with self.subTest(values=values):
                self.export("top_nakopl_vvod_msk_20260702.xlsx", commissioned(["А", "Б"], values))
                self.assert_unavailable(self.load(), "msk", "invalid_commissioned_table")

    def test_zero_area_is_a_value_but_period_is_not_inferred(self) -> None:
        self.export("top_nakopl_vvod_msk_20260702.xlsx", commissioned(["А", "Б"], [0, "1 234,5"]))
        result = self.load()
        metadata = result["nakopl_vvod_quality"]["regions"]["msk"]
        self.assertEqual(metadata["status"], "available")
        self.assertEqual(metadata["period_basis"], "collector_filename_only")
        self.assertIsNone(metadata["observation_period"])
        self.assertTrue(metadata["period_warning"])

    def test_per_year_or_unscoped_filename_is_not_cumulative_source(self) -> None:
        frame = commissioned(["ГК Москва"], [900])
        for name in ("top_nakopl_vvod_msk_2025_20260702.xlsx", "top_nakopl_vvod_msk_unknown.xlsx"):
            self.export(name, frame)
        self.assert_unavailable(self.load(), "msk", "missing_regional_export")

    def test_newest_wrong_rating_does_not_silently_fall_back_to_old_export(self) -> None:
        old = self.export("top_nakopl_vvod_msk_20260618.xlsx", commissioned(["Компания"], [100]))
        latest = self.export("top_nakopl_vvod_msk_20260702.xlsx", pd.DataFrame({"Строится, м²": [900]}))
        os.utime(old, (10, 10))
        os.utime(latest, (20, 20))
        result = self.load()
        self.assert_unavailable(result, "msk", "invalid_commissioned_table")
        self.assertTrue(result["nakopl_vvod_quality"]["regions"]["msk"]["source"].endswith(latest.name))

    def test_legacy_mart_full_copy_and_name_subset_are_rejected_in_both_modes(self) -> None:
        national = commissioned(["ГК Москва", "Компания вне Москвы"], [900, 700])
        self.export("TOP_EXCEL.xlsx", national)
        for required in ("0", "1"):
            for false_moscow in (national.copy(), national.iloc[:1].copy()):
                with self.subTest(required=required, rows=len(false_moscow)):
                    path = self.write_mart({"nakopl_vvod": {"rf": national, "msk": false_moscow}})
                    before = path.read_bytes()
                    self.da.context = replace(self.da.context, require_marts=required == "1")
                    result = self.load()
                    for region in ("rf", "msk"):
                        self.assert_unavailable(result, region, "missing_regional_export")
                    self.assertEqual(path.read_bytes(), before, "loading must not overwrite the legacy mart")

    def test_legacy_mart_recovered_only_from_real_regional_export(self) -> None:
        msk = commissioned(["Компания без географического имени"], [125])
        self.export("top_nakopl_vvod_msk_20260702.xlsx", msk)
        self.write_mart({"nakopl_vvod": {"msk": commissioned(["ГК Москва"], [900])}})
        with patch.object(core.pd, "read_excel", wraps=pd.read_excel) as reader:
            result = self.load()
            self.assertEqual(reader.call_count, 1)
        pd.testing.assert_frame_equal(result["nakopl_vvod"]["msk"], msk)

    def test_versioned_mart_keeps_provenance_without_raw_excel_parsing(self) -> None:
        msk = commissioned(["Компания"], [125])
        self.export("top_nakopl_vvod_msk_20260702.xlsx", msk)
        self.write_mart(self.load())
        with patch.object(core.pd, "read_excel", side_effect=AssertionError("unexpected raw Excel parse")):
            result = self.load()
        pd.testing.assert_frame_equal(result["nakopl_vvod"]["msk"], msk)
        self.assert_unavailable(result, "rf", "missing_regional_export")

    def test_wrong_scope_provenance_and_obsolete_version_are_revalidated(self) -> None:
        msk = commissioned(["Компания"], [125])
        raw = self.export("top_nakopl_vvod_msk_20260702.xlsx", msk)
        good = self.load()
        raw.unlink()
        for change in ("source_scope", "source", "schema_version"):
            with self.subTest(change=change):
                damaged = copy.deepcopy(good)
                quality = damaged["nakopl_vvod_quality"]
                if change == "schema_version":
                    quality[change] = 0
                elif change == "source_scope":
                    quality["regions"]["msk"][change] = "rf"
                else:
                    quality["regions"]["msk"][change] = "TOP_EXCEL.xlsx"
                self.write_mart(damaged)
                self.assert_unavailable(self.load(), "msk", "missing_regional_export")

    def test_identical_confirmed_regional_values_are_not_rejected_by_heuristic(self) -> None:
        frame = commissioned(["Компания"], [125])
        for region in ("rf", "msk"):
            self.export(f"top_nakopl_vvod_{region}_20260702.xlsx", frame)
        self.write_mart(self.load())
        result = self.load()
        for region in ("rf", "msk"):
            pd.testing.assert_frame_equal(result["nakopl_vvod"][region], frame)
            self.assertEqual(result["nakopl_vvod_quality"]["regions"][region]["status"], "available")


if __name__ == "__main__":
    unittest.main(verbosity=2)
