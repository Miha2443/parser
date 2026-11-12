"""Download-only permit details, using temporary services and a selector spy."""
import copy
from datetime import datetime
from io import BytesIO
import unittest
from unittest.mock import patch

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from openpyxl import load_workbook

from backend.auth import AuthSettings, install_auth
from backend.construction_pages_export import export_workbook
from backend.construction_pages_routes import create_router
from backend.profile_service import FilterUnavailable, SourceUnavailable
from backend.tests import test_construction_pages as fixtures


MAIN_SHEET = "\u0420\u0421 \u043e\u0431\u044a\u0435\u043a\u0442\u044b 2011-2021"
STRUCTURE_SHEET = "\u0420\u0421 \u0441\u0442\u0440\u0443\u043a\u0442\u0443\u0440\u0430 2011-2021"
METADATA_SHEET = "\u0420\u0421 \u0440\u0430\u0441\u0448\u0438\u0444\u0440\u043e\u0432\u043a\u0430"
ENDPOINT = "/api/v1/construction/permits"


class DetailsSpy:
    def __init__(self):
        self.calls = []
        self.results = []

    def select(self, permit_kind, *, month_from, month_to, year_from, year_to):
        selection = {"permitKind": permit_kind, "monthFrom": month_from, "monthTo": month_to,
                     "yearFrom": year_from, "yearTo": year_to}
        self.calls.append(copy.deepcopy(selection))
        headers = [f"Original field {column:02d}" for column in range(1, 66)]
        headers[17] = "\u041f\u043e\u0441\u043b\u0435\u0434\u043d\u044f\u044f \u0420\u0421 \u0434\u0430\u0442\u0430"
        headers[29], headers[30] = "\u041e\u0431\u0449\u0430\u044f \u043f\u043b.", "\u0416\u0438\u043b\u0430\u044f \u043f\u043b."
        headers[62] = headers[63] = "Repeated original heading"
        row = [f"Original value {column:02d}" for column in range(1, 66)]
        row[0], row[1], row[2] = "000017", "object-001", permit_kind
        row[10], row[22], row[25], row[26] = None, True, 17, -17.25
        row[17] = datetime(year_from, month_from, 12, 14, 15, 16)
        row[29], row[30] = 1000000.125, 600000.25
        result = {"headers": headers, "rows": [row], "source_rows": [17],
                  "source": {"file": "fixture-history.xlsx", "sheet": "Original source sheet",
                             "sha256": "a" * 64},
                  "selection": {"permit_kind": permit_kind, "month_from": month_from, "month_to": month_to,
                                "year_from": year_from, "year_to": year_to},
                  "notes": ["Fixture details only; no graph inputs."]}
        self.results.append(result)
        return result


class PermitDetailsExportTests(unittest.TestCase):
    def setUp(self):
        self.fixture = fixtures.ConstructionPagesTests()
        self.fixture.setUp()
        self.addCleanup(self.fixture.doCleanups)
        self.details = DetailsSpy()

    def client(self, *, authenticated=False):
        app = FastAPI()

        def call(function, *args, scope=None):
            try:
                return function(*args)
            except FilterUnavailable as exc:
                raise HTTPException(404, str(exc)) from exc
            except SourceUnavailable as exc:
                raise HTTPException(503, str(exc)) from exc

        app.include_router(create_router(self.fixture.context, call, self.fixture.permits,
                                         self.fixture.domrf, permit_history_details=self.details))
        if authenticated:
            settings = AuthSettings(enabled=True, database=self.fixture.root / "auth/test.sqlite3",
                                    secure_cookie=False)
            store = install_auth(app, settings)
            store.set_user("viewer", "fixture-viewer-password-123", "viewer")
        client = TestClient(app, base_url="http://dashboard.test", client=("203.0.113.10", 12345))
        self.addCleanup(client.close)
        return client

    def workbook(self, content):
        book = load_workbook(BytesIO(content), data_only=False)
        self.addCleanup(book.close)
        return book

    def get_payload(self, client, params):
        response = client.get(ENDPOINT, params=params)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    @staticmethod
    def values(book):
        return {sheet.title: list(sheet.values) for sheet in book}

    def assert_raw_sheet(self, sheet, result):
        self.assertEqual(sheet.max_column, 65)
        self.assertEqual(sheet.max_row, len(result["rows"]) + 1)
        self.assertEqual(list(next(sheet.values)), result["headers"])
        for actual, expected in zip(sheet.iter_rows(min_row=2, values_only=True), result["rows"]):
            self.assertEqual(list(actual), expected)
        if result["rows"]:
            self.assertIsInstance(sheet["R2"].value, datetime)
            self.assertEqual(sheet["AD2"].value, 1000000.125)
            self.assertEqual(sheet["AE2"].value, 600000.25)
            self.assertEqual(sheet["A2"].value, "000017")
            self.assertEqual(sheet["BM2"].value, "Original value 65")
            self.assertEqual(sheet["W2"].data_type, "b")
            self.assertIsNone(sheet["K2"].value)

    def test_main_january_september_all_historical_years_structure_independent_and_graph_unchanged(self):
        client = self.client()
        for kind in ("housing", "nonresidential", "total"):
            with self.subTest(kind=kind):
                self.details.calls.clear()
                self.details.results.clear()
                params = {"permit_kind": kind, "month": 9, "structure_mode": "period",
                          "structure_month_from": 3, "structure_month_to": 5,
                          "structure_year_from": 2013, "structure_year_to": 2014}
                before = self.get_payload(client, params)
                self.assertEqual(self.details.calls, [])
                response = client.get(ENDPOINT + "/export", params={**params, "required_version": before["version"]})
                self.assertEqual(response.status_code, 200, response.text[:500])
                self.assertEqual(response.headers["x-data-version"], before["version"])
                years = [row["year"] for row in before["permits"]["presentation"]["rows"]]
                self.assertCountEqual(self.details.calls, [
                    {"permitKind": kind, "monthFrom": 1, "monthTo": 9, "yearFrom": min(years), "yearTo": max(years)},
                    {"permitKind": kind, "monthFrom": 3, "monthTo": 5, "yearFrom": 2013, "yearTo": 2014},
                ])
                book = self.workbook(response.content)
                for name, first_month in ((MAIN_SHEET, 1), (STRUCTURE_SHEET, 3)):
                    result = next(r for r in self.details.results if r["selection"]["month_from"] == first_month)
                    self.assert_raw_sheet(book[name], result)
                self.assertIn(METADATA_SHEET, book.sheetnames)
                metadata = [value for row in book[METADATA_SHEET].values for value in row]
                self.assertIn("fixture-history.xlsx", metadata)
                expected_book = self.workbook(export_workbook(before))
                for name, rows in self.values(expected_book).items():
                    self.assertEqual(list(book[name].values), rows, name)
                after = self.get_payload(client, params)
                self.assertEqual(after, before)
                self.assertEqual(len(self.details.calls), 2)

    def test_annual_structure_requests_twelve_months_without_widening_main_period(self):
        client = self.client()
        params = {"permit_kind": "housing", "month": 9, "structure_mode": "annual",
                  "structure_month_from": 3, "structure_month_to": 5,
                  "structure_year_from": 2013, "structure_year_to": 2014}
        before = self.get_payload(client, params)
        response = client.get(ENDPOINT + "/export", params={**params, "required_version": before["version"]})
        self.assertEqual(response.status_code, 200, response.text[:500])
        years = [row["year"] for row in before["permits"]["presentation"]["rows"]]
        self.assertCountEqual(self.details.calls, [
            {"permitKind": "housing", "monthFrom": 1, "monthTo": 9, "yearFrom": min(years), "yearTo": max(years)},
            {"permitKind": "housing", "monthFrom": 1, "monthTo": 12, "yearFrom": 2013, "yearTo": 2014},
        ])
        self.assertIn(STRUCTURE_SHEET, self.workbook(response.content).sheetnames)

    def test_main_years_come_from_presentation_not_structure_or_forecast_years(self):
        original = self.fixture.permits.report

        def selected(*args, **kwargs):
            payload = original(*args, **kwargs)
            payload["permits"]["presentation"]["rows"] = [
                row for row in payload["permits"]["presentation"]["rows"] if row["year"] in (2014, 2016, 2025)
            ]
            return payload

        original_select = self.details.select

        def selected_details(*args, **kwargs):
            result = original_select(*args, **kwargs)
            if kwargs["year_from"] == 2014:
                # A selector's min/max range includes a historical year absent
                # from the main graph; the route must remove that gap itself.
                rows = []
                for year in (2014, 2015, 2016):
                    row = list(result["rows"][0])
                    row[17] = row[17].replace(year=year)
                    rows.append(row)
                result["rows"], result["source_rows"] = rows, [14, 15, 16]
            return result

        with patch.object(self.fixture.permits, "report", side_effect=selected), \
                patch.object(self.details, "select", side_effect=selected_details):
            client = self.client()
            params = {"month": 9, "structure_year_from": 2013, "structure_year_to": 2014}
            payload = self.get_payload(client, params)
            response = client.get(ENDPOINT + "/export", params={**params, "required_version": payload["version"]})
        self.assertEqual(response.status_code, 200, response.text[:500])
        self.assertIn({"permitKind": "total", "monthFrom": 1, "monthTo": 9,
                       "yearFrom": 2014, "yearTo": 2025}, self.details.calls)
        book = self.workbook(response.content)
        self.assertEqual([row[17].year for row in book[MAIN_SHEET].iter_rows(min_row=2, values_only=True)],
                         [2014, 2016])

    def test_stale_version_is_rejected_before_any_details_selection(self):
        client = self.client()
        params = {"permit_kind": "total", "month": 9}
        before = self.get_payload(client, params)
        with patch.object(self.details, "select", side_effect=AssertionError("must not select stale export")) as select:
            response = client.get(ENDPOINT + "/export", params={**params, "required_version": "stale"})
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.headers["x-data-version"], before["version"])
            select.assert_not_called()
        self.assertEqual(self.get_payload(client, params), before)

    def test_invalid_or_missing_export_filters_never_select_details(self):
        client = self.client()
        for query in ({}, {"required_version": "unused", "month": 13},
                      {"required_version": "unused", "permit_kind": "invalid"},
                      {"required_version": "unused", "structure_month_from": 5, "structure_month_to": 3}):
            with self.subTest(query=query):
                response = client.get(ENDPOINT + "/export", params=query)
                self.assertEqual(response.status_code, 422)
                self.assertEqual(self.details.calls, [])

    def test_exporter_is_backward_compatible_preserves_aggregates_and_does_not_mutate_inputs(self):
        payload = self.fixture.permits.report("total", 9)
        details = {key: self.details.select("total", month_from=start, month_to=end, year_from=2011, year_to=2021)
                   for key, start, end in (("main", 1, 9), ("structure", 3, 5))}
        before_payload, before_details = copy.deepcopy(payload), copy.deepcopy(details)
        baseline = self.workbook(export_workbook(payload))
        book = self.workbook(export_workbook(payload, permit_details=details))
        self.assertEqual(set(book.sheetnames) - set(baseline.sheetnames),
                         {MAIN_SHEET, STRUCTURE_SHEET, METADATA_SHEET})
        for name, rows in self.values(baseline).items():
            self.assertEqual(list(book[name].values), rows, name)
        self.assert_raw_sheet(book[MAIN_SHEET], details["main"])
        self.assert_raw_sheet(book[STRUCTURE_SHEET], details["structure"])
        self.assertEqual(payload, before_payload)
        self.assertEqual(details, before_details)

    def test_untrusted_original_headers_cells_and_metadata_are_formula_injection_safe(self):
        payload = self.fixture.permits.report("housing", 9)
        details = self.details.select("housing", month_from=1, month_to=9, year_from=2011, year_to=2021)
        dangerous = ["=1+1", " +SUM(1,1)", "\t@bad", "-external"]
        details["headers"][:4] = dangerous
        details["rows"][0][:4] = dangerous
        details["source"]["file"] = "=HYPERLINK(\"bad\")"
        details["notes"] = dangerous
        original = copy.deepcopy(details)
        book = self.workbook(export_workbook(payload, permit_details={"main": details, "structure": copy.deepcopy(details)}))
        for name in (MAIN_SHEET, STRUCTURE_SHEET):
            sheet = book[name]
            for column, value in enumerate(dangerous, 1):
                self.assertEqual(sheet.cell(1, column).value, "'" + value)
                self.assertEqual(sheet.cell(2, column).value, "'" + value)
            self.assertEqual(sheet["AA2"].value, -17.25)
            self.assertEqual(sheet["AA2"].data_type, "n")
        self.assertTrue(all(cell.data_type != "f" for sheet in book for row in sheet for cell in row))
        self.assertEqual(details, original)

    def test_empty_details_preserve_all_original_columns_and_metadata(self):
        payload = self.fixture.permits.report("nonresidential", 9)
        details = self.details.select("nonresidential", month_from=1, month_to=9, year_from=2011, year_to=2021)
        details["rows"] = []
        book = self.workbook(export_workbook(payload, permit_details={"main": details, "structure": copy.deepcopy(details)}))
        for name in (MAIN_SHEET, STRUCTURE_SHEET):
            self.assert_raw_sheet(book[name], details)
        self.assertIn(METADATA_SHEET, book.sheetnames)

    def test_viewer_cannot_download_or_trigger_details_selection(self):
        client = self.client(authenticated=True)
        login = client.post("/api/v1/auth/login", headers={"Origin": "http://dashboard.test"},
                            json={"username": "viewer", "password": "fixture-viewer-password-123"})
        self.assertEqual(login.status_code, 200, login.text)
        with patch.object(self.fixture.permits, "report", side_effect=AssertionError("viewer export must not load report")) as report:
            response = client.get(ENDPOINT + "/export", params={"required_version": "unused"})
            self.assertEqual(response.status_code, 403)
            report.assert_not_called()
        self.assertEqual(self.details.calls, [])

    def test_unavailable_detail_source_fails_download_without_changing_graphs(self):
        client = self.client()
        params = {"permit_kind": "total", "month": 9}
        before = self.get_payload(client, params)
        with patch.object(self.details, "select", side_effect=SourceUnavailable("missing or corrupt details")):
            response = client.get(ENDPOINT + "/export", params={**params, "required_version": before["version"]})
            self.assertEqual(response.status_code, 503)
            self.assertEqual(self.get_payload(client, params), before)


if __name__ == "__main__":
    unittest.main()
