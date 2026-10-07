"""Offline regression checks for download dates; only temporary files are written."""
from __future__ import annotations

import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
sys.dont_write_bytecode = True

from pipeline.download_provenance import (
    download_summary, receipt_path, record_download,
    record_successful_download, source_provenance,
)
from pipeline.downloaders.local_files import list_local_files


class DownloadProvenanceChecks(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)
        self.path = self.root / "20260919_31074_часть2.xls"
        self.path.write_bytes(b"old workbook")
        stamp = pd.Timestamp("2026-07-11T12:00:00+03:00").timestamp()
        os.utime(self.path, (stamp, stamp))
        self.df = pd.DataFrame([{"source_file": self.path.name,
                                 "loaded_at": "2026-09-29T12:00:00",
                                 "year": 2026, "month": 6}])

    def receipt(self, downloaded_at="2026-07-11T09:00:00+00:00"):
        record_download(self.path, source_id="31074", remote_date="19.09.2026")
        path = receipt_path(self.path)
        value = json.loads(path.read_text(encoding="utf-8"))
        value["downloaded_at"] = downloaded_at
        path.write_text(json.dumps(value), encoding="utf-8")

    def test_old_file_new_etl_does_not_claim_new_download(self):
        rows = source_provenance(self.df, self.root)
        self.assertEqual(rows.iloc[0]["Скачан (МСК)"], "неизвестно")
        self.assertEqual(rows.iloc[0]["Дата файла (МСК)"], "11.07.2026 12:00")
        self.assertEqual(rows.iloc[0]["Данные по"], "06.2026")
        self.assertIn("неизвестна", download_summary(rows))

    def test_receipt_date_not_remote_date_or_processing_date(self):
        self.receipt()
        rows = source_provenance(self.df, self.root)
        self.assertEqual(rows.iloc[0]["Скачан (МСК)"], "11.07.2026 12:00")
        self.assertEqual(rows.iloc[0]["Статус"], "Скачивание подтверждено")
        self.assertEqual(list_local_files(self.root, ["*.xls*"]), [self.path])

    def test_new_unreferenced_file_cannot_change_display(self):
        other = self.root / "20260929_31074_часть2.xls"
        other.write_bytes(b"new workbook")
        record_download(other)
        rows = source_provenance(self.df, self.root)
        self.assertEqual(rows["Исходный файл"].tolist(), [str(self.path)])
        self.assertEqual(rows.iloc[0]["Скачан (МСК)"], "неизвестно")
        self.path.unlink()
        rows = source_provenance(self.df, self.root)
        self.assertEqual(rows.iloc[0]["Статус"], "Исходный файл не найден")

    def test_same_name_changed_bytes_reject_receipt(self):
        self.receipt()
        self.path.write_bytes(b"changed workbook")
        self.assertEqual(source_provenance(self.df, self.root).iloc[0]["Скачан (МСК)"], "неизвестно")

    def test_download_after_processing_marks_stale_processed_data(self):
        self.receipt("2026-09-30T09:00:00+00:00")
        rows = source_provenance(self.df, self.root)
        self.assertIn("требуется ETL", rows.iloc[0]["Статус"])
        self.assertIn("требуется ETL", download_summary(rows))

    def test_two_parts_have_independent_dates_and_unknown_is_visible(self):
        self.receipt()
        second = self.root / "20180101_31074_часть1.xls"
        second.write_bytes(b"history")
        frame = pd.concat([self.df, pd.DataFrame([{"source_file": second.name,
                               "loaded_at": "2026-09-29T12:00:00", "year": 2018, "month": 12}])])
        rows = source_provenance(frame, self.root)
        self.assertEqual(len(rows), 2)
        self.assertIn("1 из 2", download_summary(rows))
        record_download(second)
        self.assertIn(" — ", download_summary(source_provenance(frame, self.root)))

    def test_invalid_receipt_and_missing_source_metadata(self):
        receipt_path(self.path).parent.mkdir()
        receipt_path(self.path).write_text("broken", encoding="utf-8")
        self.assertIn("неизвестна", download_summary(source_provenance(self.df, self.root)))
        self.assertTrue(source_provenance(pd.DataFrame(), self.root).empty)
        self.assertTrue(source_provenance(self.df.drop(columns="source_file"), self.root).empty)

    def test_success_records_receipt_but_failure_leaves_it_unchanged(self):
        @record_successful_download
        def success(indicator_id, save_dir, **kwargs):
            return self.path
        @record_successful_download
        def failure(indicator_id, save_dir, **kwargs):
            return None
        self.assertIsNone(failure("31074", self.root))
        self.assertFalse(receipt_path(self.path).exists())
        self.assertEqual(success("31074", self.root, remote_date="19.09.2026"), self.path)
        before = receipt_path(self.path).read_bytes()
        failure("31074", self.root)
        self.assertEqual(receipt_path(self.path).read_bytes(), before)


if __name__ == "__main__":
    unittest.main()
