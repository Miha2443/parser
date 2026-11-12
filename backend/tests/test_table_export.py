"""Round-trip, resource-boundary and existing-auth checks for table exports."""
from io import BytesIO
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook
from starlette.requests import Request

from backend.auth import AuthSettings, install_auth
from backend import table_export


def app():
    instance = FastAPI()
    instance.include_router(table_export.create_router())
    return instance


class TableExportTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app())

    def test_roundtrip_types_headers_ragged_rows_and_final_row(self):
        rows = [["ID", "Имя", "ID", "Flag", "Blank"],
                ["0000123", "Москва", 12.5, True, None],
                ["最後", "", -7, False], ["last", "данные", 0, None, None]]
        response = self.client.post("/api/v1/exports/table", json={"rows": rows, "sheetName": "Данные"})
        self.assertEqual(response.status_code, 200, response.text[:200] if response.status_code != 200 else "")
        self.assertTrue(response.content.startswith(b"PK"))
        self.assertIn("table.xlsx", response.headers["content-disposition"])
        self.assertEqual(response.headers["content-type"], table_export.XLSX_MEDIA_TYPE)
        book = load_workbook(BytesIO(response.content))
        self.addCleanup(book.close)
        sheet = book["Данные"]
        values = list(sheet.values)
        self.assertEqual(values[0], tuple(rows[0]))
        self.assertEqual(values[1], tuple(rows[1]))
        self.assertEqual(values[2], ("最後", None, -7, False, None))
        self.assertEqual(values[-1], tuple(rows[-1]))
        self.assertEqual(sheet["A2"].data_type, "s")
        self.assertEqual(sheet["C2"].data_type, "n")
        self.assertEqual(sheet["D2"].data_type, "b")
        self.assertEqual(sheet.freeze_panes, "A2")
        self.assertEqual(sheet.auto_filter.ref, "A1:E4")
        self.assertGreaterEqual(sheet.column_dimensions["B"].width, 10)

    def test_formula_like_strings_remain_exact_text(self):
        values = ["=1+2", "+SUM(A1)", "-1+2", "@SUM(A1)", " =1", "\t=1", "#REF!", "'001"]
        response = self.client.post("/api/v1/exports/table", json={"rows": [values]})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content), data_only=False)
        self.addCleanup(book.close)
        self.assertEqual([cell.value for cell in book.active[1]], values)
        self.assertTrue(all(cell.data_type == "s" for cell in book.active[1]))

    def test_empty_tables(self):
        for rows in ([], [[]], [[], []]):
            with self.subTest(rows=rows):
                self.assertEqual(self.client.post("/api/v1/exports/table", json={"rows": rows}).status_code, 200)

    def test_invalid_inputs_never_build_workbook(self):
        payloads = [None, [], {}, {"rows": {}}, {"rows": [1]}, {"rows": [[{}]]},
                    {"rows": [[[]]]}, {"rows": [["\x00"]]}, {"rows": [["\ud800"]]},
                    {"rows": [], "extra": 1}, {"rows": [], "sheetName": None}]
        payloads += [{"rows": [], "sheetName": name} for name in ("", " ", "a/b", "x" * 32, "'quoted")]
        with patch.object(table_export, "Workbook") as workbook:
            for payload in payloads:
                with self.subTest(payload=repr(payload)):
                    response = self.client.post("/api/v1/exports/table", content=json.dumps(payload))
                    self.assertEqual(response.status_code, 422, response.text)
            for body in (b"{", b"\xff", b'{"rows":[[NaN]]}', b'{"rows":[[Infinity]]}',
                         b'{"rows":[[1e999]]}'):
                self.assertEqual(self.client.post("/api/v1/exports/table", content=body).status_code, 422)
            workbook.assert_not_called()

    def test_limits_before_workbook_build(self):
        cases = [("MAX_ROWS", 1, [[1], [2]]), ("MAX_COLUMNS", 1, [[1, 2]]),
                 ("MAX_CELLS", 3, [[1, 2], []]), ("MAX_STRING_LENGTH", 2, [["abc"]]),
                 ("MAX_STRING_BYTES", 3, [["яя"]])]
        with patch.object(table_export, "Workbook") as workbook:
            for name, limit, rows in cases:
                with self.subTest(limit=name), patch.object(table_export, name, limit):
                    response = self.client.post("/api/v1/exports/table", json={"rows": rows})
                    self.assertEqual(response.status_code, 413, response.text)
            with patch.object(table_export, "MAX_BODY_BYTES", 10):
                self.assertEqual(self.client.post("/api/v1/exports/table", content=b"x" * 11).status_code, 413)
                self.assertEqual(self.client.post("/api/v1/exports/table", content=b"{}",
                                                 headers={"Content-Length": "11"}).status_code, 413)
            workbook.assert_not_called()

    def test_exact_limits_and_trailing_null_cells(self):
        with patch.object(table_export, "MAX_ROWS", 2), patch.object(table_export, "MAX_COLUMNS", 2), \
                patch.object(table_export, "MAX_CELLS", 4), patch.object(table_export, "MAX_STRING_LENGTH", 2), \
                patch.object(table_export, "MAX_STRING_BYTES", 4):
            response = self.client.post("/api/v1/exports/table", json={"rows": [["ab", "cd"], [1, None]]})
            self.assertEqual(response.status_code, 200)

    def test_stream_limit_without_content_length(self):
        import asyncio
        messages = iter([{"type": "http.request", "body": b"123456", "more_body": True},
                         {"type": "http.request", "body": b"78901", "more_body": False}])

        async def receive():
            return next(messages)

        request = Request({"type": "http", "method": "POST", "path": "/", "headers": []}, receive)
        endpoint = table_export.create_router().routes[0].endpoint
        with patch.object(table_export, "MAX_BODY_BYTES", 10), patch.object(table_export, "_convert") as convert:
            with self.assertRaises(HTTPException) as caught:
                asyncio.run(endpoint(request))
            self.assertEqual(caught.exception.status_code, 413)
            convert.assert_not_called()

    def test_work_runs_off_event_loop_thread(self):
        import threading
        called = []
        original = table_export._convert

        def convert(body):
            called.append(threading.current_thread().name)
            return original(body)

        with patch.object(table_export, "_convert", side_effect=convert):
            self.assertEqual(self.client.post("/api/v1/exports/table", json={"rows": [[1]]}).status_code, 200)
        self.assertEqual(called, ["AnyIO worker thread"])


class TableExportAuthTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.app = app()
        store = install_auth(cls.app, AuthSettings(enabled=True,
                             database=Path(cls.tmp.name) / "auth.sqlite3", secure_cookie=False))
        cls.password = "Test-table-export-password-2026!"
        store.set_user("analyst", cls.password, "analyst")
        store.set_user("viewer", cls.password, "viewer")

    def client(self):
        return TestClient(self.app, base_url="http://dashboard.test", client=("203.0.113.10", 12345))

    def login(self, client, username):
        result = client.post("/api/v1/auth/login", headers={"Origin": "http://dashboard.test"},
                             json={"username": username, "password": self.password})
        self.assertEqual(result.status_code, 200, result.text)
        return result.json()["csrfToken"]

    def test_auth_and_csrf_are_enforced_before_conversion(self):
        with patch.object(table_export, "_convert", wraps=table_export._convert) as convert:
            self.assertEqual(self.client().post("/api/v1/exports/table", json={"rows": [[1]]}).status_code, 401)
            viewer = self.client()
            token = self.login(viewer, "viewer")
            self.assertEqual(viewer.post("/api/v1/exports/table", json={"rows": [[1]]},
                             headers={"Origin": "http://dashboard.test", "X-CSRF-Token": token}).status_code, 403)
            analyst = self.client()
            token = self.login(analyst, "analyst")
            for headers in ({}, {"Origin": "http://dashboard.test", "X-CSRF-Token": "invalid"}):
                self.assertEqual(analyst.post("/api/v1/exports/table", json={"rows": [[1]]}, headers=headers).status_code, 403)
            convert.assert_not_called()
            result = analyst.post("/api/v1/exports/table", json={"rows": [["Header"], [42]]},
                                  headers={"Origin": "http://dashboard.test", "X-CSRF-Token": token})
            self.assertEqual(result.status_code, 200, result.text[:100] if result.status_code != 200 else "")
            self.assertTrue(result.content.startswith(b"PK"))
            convert.assert_called_once()


if __name__ == "__main__":
    unittest.main()
