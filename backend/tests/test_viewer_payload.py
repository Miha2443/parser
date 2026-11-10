"""Projection contracts only; middleware/session authorization is tested separately."""
import copy
import json
from pathlib import Path
import unittest

from backend.viewer_payload import viewer_payload


PRIVATE = "postgresql://dbuser:DO_NOT_DISCLOSE@database/private"
META = {"schemaVersion": 1, "version": "generation", "generatedAt": "2026-11-10T10:00:00Z",
        "source": {"date": "2026-11-03", "files": ["/runtime/releases/private/source.xlsx"],
                   "issues": [["warning", PRIVATE]], "fileEvidence": "selected",
                   "recordedSources": [PRIVATE], "downloadSummary": PRIVATE,
                   "dateEvidence": PRIVATE, "recordedSourceEvidence": PRIVATE,
                   "provenance": {"columns": [{"id": "file", "label": "File"}], "rows": [{"file": PRIVATE}]}},
        "notes": [PRIVATE], "credentials": {"db_password": PRIVATE}}


def inject_unknown(value):
    if isinstance(value, dict):
        return {**{key: inject_unknown(item) for key, item in value.items()},
                "futureMetadata": {"db_password": PRIVATE}}
    if isinstance(value, list):
        return [inject_unknown(item) for item in value]
    return value


class ViewerPayloadTests(unittest.TestCase):
    def assert_private_absent(self, value):
        text = json.dumps(value, ensure_ascii=False, allow_nan=False)
        for forbidden in ("DO_NOT_DISCLOSE", "/runtime/", "source.xlsx", "futureMetadata", "db_password"):
            self.assertNotIn(forbidden, text)

    def test_unknown_and_non_graph_routes_fail_closed(self):
        for path in ("/api/v1/future", "/api/v1/availability", "/api/v1/updates", "/api/v1/health",
                     "/api/v1/tdm/catalog", "/api/v1/profile/export", "/docs", "/openapi.json",
                     "/api/v1/construction-tree/history/2026-11-01/export", "/api/v1/map/export",
                     "/api/v1/economics/salary/report/unexpected", "/api/v1/construction/tree/future"):
            with self.subTest(path=path), self.assertRaises(ValueError):
                viewer_payload(path, META)
        for path, value in ((None, {}), ("/api/v1/profile", [])):
            with self.assertRaises(ValueError):
                viewer_payload(path, value)

    def test_parent_graph_inventory_and_projection_inventory_match_exactly(self):
        from backend.auth import GRAPH_PATHS
        from backend.viewer_payload import _ROUTES
        self.assertEqual(set(_ROUTES), set(GRAPH_PATHS))
        for path in GRAPH_PATHS:
            with self.subTest(path=path):
                self.assertIsInstance(viewer_payload(path, {}), dict)

    def test_private_declared_table_columns_and_unlisted_row_fields_are_not_public(self):
        raw = {"tables": [{"id": "aggregate", "title": "Aggregate",
                           "columns": [{"id": "value", "label": "Value"},
                                       {"id": "source_path", "label": "File"}, {"id": "db_password", "label": "Password"}],
                           "rows": [{"value": 0, "source_path": PRIVATE, "db_password": PRIVATE, "futureMetadata": PRIVATE}]}]}
        projected = viewer_payload("/api/v1/sales", raw)
        self.assert_private_absent(projected)
        self.assertEqual(projected["tables"][0]["rows"], [{"value": 0}])
        self.assertEqual(projected["tables"][0]["columns"], [{"id": "value", "label": "Value"}])

    def test_profile_snapshot_removes_all_raw_tables_without_mutation(self):
        path = Path(__file__).resolve().parents[2] / "frontend/public/profile-snapshot.json"
        raw = json.loads(path.read_text(encoding="utf-8"))
        raw = inject_unknown(raw)
        raw["version"] = "profile-version"
        raw["notes"] = [PRIVATE]
        before = copy.deepcopy(raw)
        projected = viewer_payload("/api/v1/profile", raw)
        self.assertEqual(raw, before)
        self.assert_private_absent(projected)
        self.assertEqual(projected["sourceDates"],
                         {key: value for key, value in before["sourceDates"].items() if key != "futureMetadata"})
        self.assertEqual(projected["provenance"], {"sources": {}, "issues": []})
        for original, profile in zip(raw["profiles"], projected["profiles"]):
            self.assertEqual(profile["notes"] if "notes" in profile else [], [])
            self.assertEqual(profile["sourceNotes"], [])
            for table in profile["objects"].values():
                self.assertEqual(table, {"columns": [], "rows": [], "rowCount": 0, "sourceNote": ""})
            self.assertNotIn("row", profile["rasprod"]["msk"])
            self.assertNotIn("aggregateRow", profile["apartments"])
            self.assertNotIn("perDevRow", profile["apartments"])
            self.assertNotIn("cardRow", profile["delays"])
            self.assertNotIn("quality", profile["ratings"])
            self.assertEqual(profile["annual"]["rows"][0]["totalM2"], original["annual"]["rows"][0]["totalM2"])
        projected["profiles"][0]["annual"]["rows"].clear()
        self.assertTrue(raw["profiles"][0]["annual"]["rows"])

    def test_operational_keeps_chart_rows_numbers_zero_null_and_month_coverage(self):
        raw = {**copy.deepcopy(META), "selection": {"month": 9, "year": 2026},
               "tree": {"total": 0, "missing": None, "futureMetadata": {"password": PRIVATE}},
               "sourceDetails": {"file": PRIVATE, "date": "2026-11-03"},
               "structure": {"year": 2026, "requestedMonth": 9, "actualMonth": 8, "sourceDate": "2026-08-31",
                             "rows": [{"id": "total", "label": "Total", "value": 0, "knownSum": None}]},
               "records": [{"source_file": PRIVATE}], "tables": [{"id": "housing", "title": "Housing",
                   "columns": [{"id": "year", "label": "Year"}, {"id": "amount", "label": "Amount"}],
                   "rows": [{"year": 2026, "amount": 0, "rawSource": PRIVATE}],
                   "chart": {"x": ["2026"], "period": [{"value": 0}], "remainder": [{"value": None}],
                             "totals": [{"value": 0}], "growth": [{"value": None}], "actualMonths": [8]},
                   "annualForecasts": [{"year": 2027, "value": 100.123456789}]}]}
        result = viewer_payload("/api/v1/commissioning/operational", inject_unknown(raw))
        self.assert_private_absent(result)
        self.assertEqual(result["tree"], {"total": 0, "missing": None})
        self.assertEqual(result["tables"][0]["chart"], raw["tables"][0]["chart"])
        self.assertEqual(result["tables"][0]["rows"], [{"year": 2026, "amount": 0}])
        self.assertEqual(result["tables"][0]["annualForecasts"], raw["tables"][0]["annualForecasts"])
        self.assertEqual(result["structure"]["actualMonth"], 8)
        self.assertEqual(result["sourceDetails"], {"file": None, "date": "2026-11-03"})

    def test_permit_metadata_cells_removed_but_forecast_and_area_variants_survive(self):
        forecast = {"unit": "тыс. м²", "unitBasis": "assumed_thousand_m2", "notes": [PRIVATE],
                    "source": {"file": PRIVATE, "sheet": "Прогнозы"},
                    "rows": [{"year": 2026, "value": 0, "housing_living": None, "cells": {"value": "D103"}}]}
        raw = {**copy.deepcopy(META), "forecast": forecast, "areaVariants": {"living": {"forecast": forecast}, "full": {"forecast": forecast}}}
        result = viewer_payload("/api/v1/construction/permits", inject_unknown(raw))
        self.assert_private_absent(result)
        for f in (result["forecast"], result["areaVariants"]["living"]["forecast"], result["areaVariants"]["full"]["forecast"]):
            self.assertEqual(f["rows"], [{"year": 2026, "value": 0, "housing_living": None, "cells": {}}])
            self.assertEqual(f["source"], {"file": None, "sheet": "Прогнозы"})

    def test_annual_precision_needed_for_chart_labels_has_no_cell_evidence(self):
        raw = {**copy.deepcopy(META), "charts": [{"id": "housing", "title": "Housing", "columns": [], "rows": [],
               "categoryProvenance": [{"year": 2026, "living_source_precision_million": .001,
                                       "living_source_cell": "F18", "rawRows": [123], "filename": PRIVATE}],
               "rawCategoryValues": [{"year": 2026, "sourceFile": PRIVATE}]}]}
        result = viewer_payload("/api/v1/commissioning/annual", inject_unknown(raw))
        self.assert_private_absent(result)
        self.assertEqual(result["charts"][0]["categoryProvenance"], [{"year": 2026, "living_source_precision_million": .001}])
        self.assertNotIn("rawCategoryValues", result["charts"][0])

    def test_economics_requires_empty_typed_provenance_but_keeps_interactive_rows(self):
        raw = {**copy.deepcopy(META), "family": "salary", "selection": {"month": 9, "period": "month"},
               "charts": [{"id": "salary", "title": "Salary", "columns": [{"id": "value", "label": "Value"}],
                           "rows": [{"value": 0, "sql": PRIVATE}], "series": [{"id": "x", "name": "X", "points": [{"x": "2026", "y": None}]}]}]}
        for endpoint in ("/api/v1/economics/salary", "/api/v1/economics/salary/report"):
            result = viewer_payload(endpoint, inject_unknown(raw))
            self.assert_private_absent(result)
            self.assertEqual(result["source"]["provenance"], {"columns": [], "rows": []})
            self.assertEqual(result["source"]["files"], [])
            self.assertEqual(result["source"]["issues"], [])
            self.assertEqual(result["charts"][0]["rows"], [{"value": 0}])

    def test_tree_current_history_and_history_catalog_preserve_dates_not_source_identifiers(self):
        snapshot = {"date": "2026-11-01", "capturedAt": "2026-11-02T04:00:00Z", "publishedAt": "2026-10-30T14:00:00Z",
                    "delayedCapture": False, "release": "/runtime/releases/private", "sha256": "a" * 64}
        raw = {**copy.deepcopy(META), "unit": "million_m2", "snapshot": snapshot,
               "source": {"file": PRIVATE, "sheet": "Дерево текущ стр во", "column": "Итог", "downloadedAt": META["generatedAt"], "fileDate": "2026-11-10"},
               "metrics": [{"id": "total", "label": "Total", "value": 0, "cell": "L938"}],
               "nonresidentialSectors": {"status": "available", "items": [{"id": "one", "label": "One", "value": 0, "rows": [789, 1234], "count": 2}]}}
        for path in ("/api/v1/construction/tree", "/api/v1/construction-tree/history/2026-11-01"):
            result = viewer_payload(path, inject_unknown(raw))
            self.assert_private_absent(result)
            self.assertEqual(result["metrics"][0]["cell"], "A1")
            self.assertEqual(result["metrics"][0]["value"], 0)
            self.assertEqual(result["nonresidentialSectors"]["items"][0]["rows"], [2, 3])
            self.assertEqual(result["snapshot"]["date"], snapshot["date"])
            self.assertEqual(result["snapshot"]["sha256"], "0" * 64)
        catalog = viewer_payload("/api/v1/construction-tree/history", {"schemaVersion": 1, "snapshots": [snapshot]})
        self.assertEqual(catalog["snapshots"][0]["release"], "restricted")

    def test_mortgage_urls_are_fixed_public_constants_never_user_supplied_secrets(self):
        for endpoint in ("mortgage", "preferential-mortgage"):
            raw = {**copy.deepcopy(META), "source": {**META["source"], "url": PRIVATE,
                                                       "collectedAt": META["generatedAt"]}}
            result = viewer_payload(f"/api/v1/{endpoint}", raw)
            self.assert_private_absent(result)
            self.assertTrue(result["source"]["url"].startswith("https://"))
            self.assertNotIn("@", result["source"]["url"])
            self.assertEqual(result["source"]["collectedAt"], META["generatedAt"])

    def test_unsafe_strings_in_known_labels_are_not_a_secret_escape_hatch(self):
        for label in (PRIVATE, "DB_PASSWORD=DO_NOT_DISCLOSE", r"C:\Users\private\book.xlsx", "Bearer DO_NOT_DISCLOSE"):
            raw = {**copy.deepcopy(META), "metrics": [{"id": "one", "label": label, "unit": "km", "value": 0, "digits": 1}]}
            projected = viewer_payload("/api/v1/sales", raw)
            self.assert_private_absent(projected)
            self.assertEqual(projected["metrics"][0]["label"], "restricted")

    def test_metadata_projection_is_detached_idempotent_and_does_not_fabricate_numbers(self):
        raw = inject_unknown({**copy.deepcopy(META), "family": "accounts", "charts": [], "tables": []})
        before = copy.deepcopy(raw)
        once = viewer_payload("/api/v1/economics/accounts/report", raw)
        self.assertEqual(raw, before)
        self.assertEqual(viewer_payload("/api/v1/economics/accounts/report", once), once)
        once["source"]["files"].append("changed")
        self.assertNotEqual(raw["source"]["files"], once["source"]["files"])


if __name__ == "__main__":
    unittest.main()
