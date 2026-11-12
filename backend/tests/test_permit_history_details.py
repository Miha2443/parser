"""Isolated export-detail selection and typed snapshot tests."""
from datetime import datetime
import gzip
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from backend.permit_history_details import (EXPECTED_HEADERS, PermitHistoryDetails,
                                           _eligible, _encode, _positive_area, extract_history, write_history)


class PermitHistoryDetailsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "details.json.gz"
        self.headers = [None] * 65
        self.headers[0] = 1
        for i, label in EXPECTED_HEADERS.items():
            self.headers[i] = label

    def row(self, when, *, code="01 Жилые", general=10, living=None):
        row = [None] * 65
        row[0:5] = [7, "UIN-1", code, "01 Новое строительство", "08 Строительство отменено"]
        row[15], row[17], row[29], row[30] = datetime(2009, 1, 1), when, general, living
        row[37], row[40], row[64] = True, "=saved text", "https://example.test"
        return row

    def save(self, rows):
        payload = {"schema": 1, "headers": self.headers,
                   "source": {"file": "input.xlsx", "sheet": "Сроки строительства1", "sha256": "test"},
                   "records": [{"source_row": i, "values": [_encode(v) for v in row]}
                               for i, row in enumerate(rows, 2)]}
        write_history(payload, self.path)
        return PermitHistoryDetails(self.path)

    def select(self, service, kind="total", **kwargs):
        return service.select(kind, **{"month_from": 1, "month_to": 9,
                                      "year_from": 2011, "year_to": 2021, **kwargs})

    def test_month_boundaries_year_sort_and_raw_types(self):
        rows = [self.row(datetime(2021, 9, 30, 23, 59)), self.row(datetime(2011, 1, 1)),
                self.row(datetime(2011, 10, 1))]
        service = self.save(rows)
        result = self.select(service)
        self.assertEqual(result["rows"], [rows[1], rows[0]])
        self.assertEqual(result["source_rows"], [3, 2])
        self.assertEqual(result["headers"], self.headers)
        self.assertTrue(all(len(row) == 65 for row in result["rows"]))
        self.assertIs(type(result["rows"][0][0]), int)
        self.assertIs(type(result["rows"][0][37]), bool)
        self.assertIsInstance(result["rows"][0][17], datetime)
        self.assertEqual(self.select(service, month_from=10, month_to=10)["rows"], [rows[2]])

    def test_kind_includes_infrastructure_and_dormitories_without_status_filter(self):
        rows = [self.row(datetime(2018, 1, 1), code=code)
                for code in ("01 Жилые", "16 Объекты метрополитена", "18 Улицы и дороги", "22 Общежитие", None)]
        service = self.save(rows)
        self.assertEqual(self.select(service, "housing")["rows"], rows[:1])
        self.assertEqual(self.select(service, "nonresidential")["rows"], rows[1:])

    def test_positive_finite_numeric_only(self):
        for value in (None, 0, -1, "5", True, float("nan"), float("inf"), -float("inf")):
            self.assertFalse(_positive_area(value), repr(value))
            self.assertFalse(_eligible(self.row(datetime(2018, 1, 1), general=value)))
            self.assertTrue(_eligible(self.row(datetime(2018, 1, 1), general=value, living=1)))
        for value in (1, 0.01):
            self.assertTrue(_positive_area(value))
        row = self.row(datetime(2018, 1, 1), general=float("nan"), living=2.5)
        result = self.select(self.save([row]))["rows"][0]
        self.assertEqual(result[30], 2.5)

    def test_no_historical_intersection_and_mutation_isolation(self):
        service = self.save([self.row(datetime(2018, 1, 1))])
        for lo, hi in ((2022, 2026), (2011, 2010), (2000, 2010)):
            result = self.select(service, year_from=lo, year_to=hi)
            self.assertEqual(result["rows"], [])
            self.assertEqual(result["headers"], self.headers)
            self.assertEqual(result["source"]["file"], "input.xlsx")
        result = self.select(service)
        result["rows"][0][1] = "changed"
        result["headers"][1] = "changed"
        result["source"]["file"] = "changed"
        self.assertEqual(self.select(service)["rows"][0][1], "UIN-1")
        self.assertEqual(self.select(service)["headers"], self.headers)

    def test_invalid_month_and_schema(self):
        service = self.save([])
        for lo, hi in ((0, 9), (1, 13), (10, 9), (True, 9)):
            with self.assertRaises(ValueError):
                self.select(service, month_from=lo, month_to=hi)
        payload = json.loads(gzip.decompress(self.path.read_bytes()))
        payload["schema"] = 2
        write_history(payload, self.path)
        with self.assertRaises(ValueError):
            self.select(service)

    def test_missing_or_corrupt_asset_does_not_produce_fake_empty_details(self):
        service = PermitHistoryDetails(self.path)
        with self.assertRaises(FileNotFoundError):
            self.select(service)
        self.path.write_bytes(b"not a gzip snapshot")
        with self.assertRaises(OSError):
            self.select(service)

    def test_import_filter_headers_and_reproducible_gzip(self):
        source = Path(self.tmp.name) / "source.xlsx"
        source.write_bytes(b"mock stable workbook")
        rows = [self.headers, self.row(datetime(2010, 12, 31)), self.row(datetime(2022, 1, 1)),
                self.row(None), self.row(datetime(2011, 1, 1), general=0),
                self.row(datetime(2021, 9, 30), general=None, living=1)]

        class Sheet:
            def iter_rows(self, **kwargs):
                return iter(rows)

        class Book:
            sheetnames = ["Сроки строительства1"]
            def close(self):
                pass
            def __getitem__(self, key):
                return Sheet()

        with patch("backend.permit_history_details.load_workbook", return_value=Book()):
            payload = extract_history(source)
            self.assertEqual(len(payload["records"]), 1)
            self.assertEqual(payload["records"][0]["source_row"], 6)
            write_history(payload, self.path)
            first = self.path.read_bytes()
            write_history(extract_history(source), self.path)
            self.assertEqual(first, self.path.read_bytes())
            self.headers[17] = "wrong date column"
            with self.assertRaises(ValueError):
                extract_history(source)

    def test_source_read_race_is_rejected(self):
        source = Path(self.tmp.name) / "source.xlsx"
        source.write_bytes(b"first version")

        class Book:
            sheetnames = ["Сроки строительства1"]
            def __getitem__(book, key):
                return book
            def iter_rows(book, **kwargs):
                return iter([self.headers])
            def close(book):
                source.write_bytes(b"replacement version")

        with patch("backend.permit_history_details.load_workbook", return_value=Book()):
            with self.assertRaisesRegex(RuntimeError, "changed during import"):
                extract_history(source)


if __name__ == "__main__":
    unittest.main()
