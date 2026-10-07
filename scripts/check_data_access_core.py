"""Offline integration checks: independent loaders, isolation and cache freshness."""
from __future__ import annotations

import contextlib
import io
import json
import logging
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.dont_write_bytecode = True
logging.disable(logging.CRITICAL)

from pipeline.data_access import DataAccess, DataContext
from pipeline.source_records import file_sha256, source_record_id
from app import data_access as ui


def same(left, right):
    if isinstance(left, pd.DataFrame):
        pd.testing.assert_frame_equal(left, right)
    elif isinstance(left, dict):
        assert set(left) == set(right), (set(left), set(right))
        for key in left:
            same(left[key], right[key])
    else:
        assert left == right, (left, right)


class CoreChecks(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.access = DataAccess(DataContext(self.root))
        self.env = patch.dict(os.environ, {"PARSER_ROOT": str(self.root),
                                          "PARSER_DOWNLOADS": str(self.root / "downloads"),
                                          "PARSER_USE_REALTY_MARTS": "1", "PARSER_REQUIRE_REALTY_MARTS": "0"})
        self.env.start()
        self.addCleanup(self.env.stop)
        ui._cached_load.clear()
        self.addCleanup(ui._cached_load.clear)

    def mart(self, name, value, sources=None):
        directory = self.root / "data/marts/realty"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / f"{name}.pkl"
        pd.to_pickle(value, path)
        (directory / "manifest.json").write_text(json.dumps({"marts": {name: {
            "file": path.relative_to(self.root).as_posix(), "sources": sources or [],
        }}}), encoding="utf-8")
        return path

    def escrow(self, name, value):
        path = self.root / "data/raw/realty/escrow_manual" / name
        path.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame({"amount": [value]}).to_excel(path, sheet_name="Выгрузка", startrow=1, index=False)
        return path

    def test_core_and_builder_never_import_ui_or_change_environment(self):
        self.escrow("input.xlsx", 33)
        self.mart("escrow_manual", pd.DataFrame({"amount": [11]}))
        code = """
import importlib.abc, os, sys
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname == 'streamlit' or fullname.startswith(('streamlit.', 'app.')):
            raise AssertionError('UI import: '+fullname)
sys.meta_path.insert(0, Guard())
before=dict(os.environ)
from pipeline import build_realty_marts as builder
from pipeline.data_access import DataAccess
access=builder._prepare_imports()
assert not access.context.use_marts and not access.context.require_marts
assert builder.build() == 0
assert os.environ == before
assert builder.ROOT == __import__('pathlib').Path(os.environ['PARSER_ROOT'])
assert 'streamlit' not in sys.modules
"""
        environment = os.environ.copy()
        environment["PARSER_REQUIRE_REALTY_MARTS"] = "1"
        result = subprocess.run([sys.executable, "-B", "-c", code], cwd=ROOT, env=environment,
                                capture_output=True, text=True, encoding="utf-8")
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertTrue((self.root / "data/marts/realty/manifest.json").is_file())
        rebuilt = pd.read_pickle(self.root / "data/marts/realty/escrow_manual.pkl")
        self.assertEqual(rebuilt["amount"].tolist(), [33], "builder must parse raw, not reuse 11 from the mart")

    def test_independent_contexts_do_not_leak_paths_or_manifest_cache(self):
        self.mart("escrow_manual", pd.DataFrame({"amount": [11]}))
        other = self.root / "other"
        other_dir = other / "data/marts/realty"
        other_dir.mkdir(parents=True)
        pd.DataFrame({"amount": [22]}).to_pickle(other_dir / "escrow_manual.pkl")
        (other_dir / "manifest.json").write_text(json.dumps({"marts": {"escrow_manual": {}}}))
        second = DataAccess(DataContext(other))
        self.assertEqual(self.access.load_escrow_manual()["amount"].tolist(), [11])
        self.assertEqual(second.load_escrow_manual()["amount"].tolist(), [22])
        self.assertEqual(self.access.load_escrow_manual()["amount"].tolist(), [11])

    def test_vvod_registry_and_loader_select_same_root_when_both_exist(self):
        primary = self.root / "data/raw/realty/vvod"
        secondary = self.root / "vvod"
        for directory in (primary, secondary):
            directory.mkdir(parents=True)
            (directory / "emiss_34118_base.xls").write_bytes(b"source metadata fixture")
        for name in ("vvod_static", "emiss_34118"):
            with self.subTest(mart=name):
                files = self.access.source_files(name)
                self.assertEqual(files, [primary / "emiss_34118_base.xls"])
                sources = [{"path": p.relative_to(self.root).as_posix(),
                            "size_bytes": p.stat().st_size, "mtime_ns": p.stat().st_mtime_ns} for p in files]
                value = {"msk_total": pd.DataFrame({"year": [2024]})} if name == "vvod_static" else pd.DataFrame({"year": [2024]})
                self.mart(name, value, sources)
                required = DataAccess(DataContext(self.root, require_marts=True))
                same(getattr(required, "load_" + name)(), value)
    def test_cache_refreshes_after_atomic_publication_with_preserved_mtime(self):
        path = self.mart("escrow_manual", pd.DataFrame({"amount": [11]}))
        self.assertEqual(ui.load_escrow_manual()["amount"].tolist(), [11])
        stat = path.stat()
        replacement = path.with_suffix(".new")
        pd.DataFrame({"amount": [22]}).to_pickle(replacement)
        os.utime(replacement, ns=(stat.st_atime_ns, stat.st_mtime_ns))
        replacement.replace(path)
        self.assertEqual(ui.load_escrow_manual()["amount"].tolist(), [22])

    def test_cache_refreshes_on_manifest_error(self):
        self.mart("escrow_manual", pd.DataFrame({"amount": [11]}))
        self.assertEqual(ui.load_escrow_manual()["amount"].tolist(), [11])
        manifest = self.root / "data/marts/realty/manifest.json"
        manifest.write_text(json.dumps({"marts": {"escrow_manual": {"error": "quarantined"}}}))
        with patch.dict(os.environ, {"PARSER_REQUIRE_REALTY_MARTS": "1"}):
            with self.assertRaisesRegex(RuntimeError, "manifest marks"):
                ui.load_escrow_manual()

    def test_new_raw_file_invalidates_warm_optional_mart(self):
        old = self.escrow("old.xlsx", 33)
        source = {"path": old.relative_to(self.root).as_posix(), "size_bytes": old.stat().st_size,
                  "mtime_ns": old.stat().st_mtime_ns}
        self.mart("escrow_manual", pd.DataFrame({"amount": [11]}), [source])
        self.assertEqual(ui.load_escrow_manual()["amount"].tolist(), [11])
        new = self.escrow("new.xlsx", 44)
        os.utime(new, ns=(old.stat().st_atime_ns, old.stat().st_mtime_ns + 10_000_000_000))
        self.assertEqual(ui.load_escrow_manual()["amount"].tolist(), [44])
        with patch.dict(os.environ, {"PARSER_REQUIRE_REALTY_MARTS": "1"}):
            with self.assertRaisesRegex(RuntimeError, "stale realty mart"):
                ui.load_escrow_manual()

    def test_unchanged_source_directory_reuses_index_and_ignores_archives(self):
        directory = self.root / "data/raw/realty/erzrf/cards"
        directory.mkdir(parents=True)
        active = directory / "cards_20260701.xlsx"
        active.write_bytes(b"active")
        archive = directory / "_archive"
        archive.mkdir()
        (archive / "cards_20260702.xlsx").write_bytes(b"old archive")
        self.assertEqual(self.access.source_files("erzrf_cards"), [active])
        with patch("pipeline.data_access.os.scandir", side_effect=AssertionError("unnecessary directory scan")):
            self.assertEqual(self.access.source_files("erzrf_cards"), [active])
        token = self.access.data_version("load_erzrf_cards")
        active.write_bytes(b"new content")
        self.assertNotEqual(token, self.access.data_version("load_erzrf_cards"))

    def test_processed_and_derived_changes_refresh_public_statistics(self):
        processed = self.root / "data/processed"
        derived = self.root / "data/derived"
        processed.mkdir(parents=True)
        derived.mkdir()
        columns = {"indicator_id": ["avg_salary"], "section": ["salary"], "view": ["salary"],
                   "region": ["msk"], "year": [2024], "month": [1], "quarter": [1],
                   "period_type": ["month"], "value": [10]}
        salary = pd.DataFrame(columns)
        salary.to_pickle(processed / "avg_salary.pkl")
        self.assertEqual(ui.load_salary()["value"].tolist(), [10, 10])
        salary["value"] = 20
        salary.to_pickle(processed / "avg_salary.pkl")
        self.assertEqual(ui.load_salary()["value"].tolist(), [20, 20])
        extra = salary.copy()
        extra["year"] = 2023
        extra["value"] = 30
        extra.to_csv(derived / "salary.csv", index=False)
        self.assertEqual(set(ui.load_salary()["value"]), {20, 30})
        pd.DataFrame({"value": [40]}).to_pickle(processed / "ipc.pkl")
        self.assertEqual(ui.load_ipc()["value"].tolist(), [40])
        pd.DataFrame({"value": [50]}).to_pickle(processed / "ipc.pkl")
        self.assertEqual(ui.load_ipc()["value"].tolist(), [50])

    def test_all_public_loader_families_equal_core_on_empty_isolated_root(self):
        names = ("load_salary", "load_ipc", "load_national_accounts", "load_kvartirografia",
                 "load_monitoring_2_0", "load_erzrf_top", "load_erzrf_cards", "load_escrow_manual",
                 "load_rasprodannost", "load_vvod_static", "load_emiss_34118",
                 "load_emiss_34118_periods", "load_monitoring_2011_2026_static")
        for name in names:
            with self.subTest(loader=name):
                same(getattr(self.access, name)(), getattr(ui, name)())

    def test_monitoring_provenance_precedes_filtering_and_concat(self):
        path = self.root / "monitoring.xlsx"
        rows = {"УИН": ["same", "same"], "Группа компаний": ["А", "А"],
                "Год ввода по Мосстату": [2024, 2024], "Общая площадь": [10, 20],
                "Жилая площадь": [5, 6], "Отрасли": ["Жилые объекты"] * 2,
                "Группировка": ["Жилье"] * 2}
        rv = pd.DataFrame(rows)
        old = pd.concat([rv.iloc[[0]], pd.DataFrame([{c: None for c in rv.columns}]), rv.iloc[[1]]], ignore_index=True)
        oks = rv.assign(Назначение="Жилье")
        with pd.ExcelWriter(path) as writer:
            rv.to_excel(writer, sheet_name="Реестр РВ", index=False)
            oks.to_excel(writer, sheet_name="Реестр ОКС", index=False)
            old.to_excel(writer, sheet_name="Лист4", index=False)
        # Explicit source bypasses even require_marts=True; no prepared mart exists.
        access = DataAccess(DataContext(self.root, require_marts=True))
        value = access.load_monitoring_2_0(source_path=path, include_provenance=True)
        self.assertEqual(value["rv"]["source_row"].tolist(), [2, 3, 2, 4])
        self.assertEqual(len(set(value["rv"]["source_record_id"])), 4)
        self.assertEqual(value["oks"]["source_row"].tolist(), [2, 3])
        digest = file_sha256(path)
        for frame in (value["rv"], value["oks"]):
            for _, row in frame.iterrows():
                self.assertEqual(row["source_record_id"], source_record_id(digest, row["source_sheet"], row["source_row"]))
        plain = access.load_monitoring_2_0(source_path=path)
        for family in ("rv", "oks"):
            extras = set(value[family].columns) - set(plain[family].columns)
            pd.testing.assert_frame_equal(value[family].drop(columns=list(extras)), plain[family])


if __name__ == "__main__":
    unittest.main(verbosity=2)
