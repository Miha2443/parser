"""Candidate/live construction contracts using real service fixture reports."""
import copy
from io import BytesIO
import json
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from urllib.parse import parse_qs, urlsplit

from backend.tests import test_construction_pages as fixtures
from docker.coverage import (CONSTRUCTION_SALES_FIELDS, validate_domrf_page,
                             validate_pages, validate_permits_page)
from pipeline.source_availability import route_sources


class ConstructionPageCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        fixture = fixtures.ConstructionPagesTests()
        fixture.setUp()
        cls.addClassCleanup(fixture.doCleanups)
        with fixture.client() as client:
            cls.permits = {}
            for kind in ("housing", "nonresidential", "total"):
                response = client.get("/api/v1/construction/permits?permit_kind=" + kind)
                if response.status_code != 200:
                    raise AssertionError(response.text)
                cls.permits[kind] = response.json()
            cls.domrf = {region: client.get("/api/v1/construction/domrf?region=" + region).json()
                         for region in ("msk", "rf")}

    def get(self, route, *, absent=frozenset()):
        path, query = urlsplit(route).path, parse_qs(urlsplit(route).query)
        blocked = route_sources(path) & absent
        if blocked:
            return {"sourceUnavailable": True, "sources": sorted(blocked)}
        if path == "/api/v1/construction/permits":
            return copy.deepcopy(self.permits[query.get("permit_kind", ["total"])[0]])
        if path == "/api/v1/construction/domrf":
            return copy.deepcopy(self.domrf[query.get("region", ["msk"])[0]])
        # Other page families are outside this focused contract; keep them data-bearing.
        reports = {
            "/api/v1/catalog": {"controls": {"developers": [{"developerKey": "fixture", "regions": ["msk"]}]}},
            "/api/v1/profile": {"profiles": [{}]},
            "/api/v1/apartments": {"apartments": [{"count": 0}], "distribution": [{}], "developers": [{"name": "fixture"}]},
            "/api/v1/apartments/developer": {"summary": {"countThousand": 0}},
            "/api/v1/sales": {"metrics": [{"value": 0}]},
            "/api/v1/construction": {"metrics": [{"id": "living", "value": 0}], "permits": {"rows": [{}]},
                                      "sales": dict.fromkeys(CONSTRUCTION_SALES_FIELDS, 0)},
            "/api/v1/commissioning/annual": {"charts": [{"rows": [{}]}]},
            "/api/v1/commissioning/operational": {"tables": [{"rows": [{}]}]},
            "/api/v1/map": {"rows": [{}]},
            "/api/v1/linear": {"summary": {"count": 1}},
        }
        if path in {"/api/v1/economics/" + family for family in ("salary", "ipc", "accounts")}:
            return {"charts": [{"series": [{"points": [{"y": 0}]}]}]}
        return copy.deepcopy(reports.get(path, {}))

    @staticmethod
    def previous_permits(payload):
        payload.pop("contractRevision", None)
        payload["selection"].pop("structure", None)
        payload["controls"].pop("structure", None)
        payload["permits"].pop("presentation", None)
        for block in payload["breakdowns"]:
            block.pop("aggregateField", None)
            for row in block["rows"]:
                row.pop("periodComplete", None)
                row.pop("annualComplete", None)
        return payload

    def test_shared_coverage_queries_new_pages_all_kinds_and_regions_without_a_history_bundle(self):
        calls = []

        def get(route):
            calls.append(route)
            return self.get(route)

        before = copy.deepcopy((self.permits, self.domrf))
        validate_pages(get)
        for kind in self.permits:
            self.assertIn("/api/v1/construction/permits?permit_kind=" + kind, calls)
        for region in self.domrf:
            self.assertIn("/api/v1/construction/domrf?region=" + region, calls)
            for block in ("construction", "sales"):
                self.assertEqual(len(self.domrf[region][block]["history"]), 1)
                self.assertNotIn("construction_history.json", self.domrf[region][block]["history"][0]["sourceFile"])
        self.assertEqual((self.permits, self.domrf), before)

    def test_candidate_validation_rejects_prior_permits_api_shape(self):
        from docker.publication import validate_api

        def get(route):
            payload = self.get(route)
            return self.previous_permits(payload) if urlsplit(route).path == "/api/v1/construction/permits" else payload

        with patch("docker.coverage.validate_sources"), patch("pipeline.source_availability.read_publication", return_value={}), \
                patch("backend.app.create_app"), patch("fastapi.testclient.TestClient") as client:
            response = client.return_value.__enter__.return_value.get
            response.side_effect = lambda route: SimpleNamespace(status_code=200, json=lambda: get(route))
            with self.assertRaisesRegex(RuntimeError, "contractRevision 2"):
                validate_api()

    def test_live_readiness_rejects_prior_permits_shape_too(self):
        from docker.readiness import probe

        def open_url(url, **kwargs):
            route = url.split("http://fixture", 1)[1]
            payload = {"release": "pinned", "status": "ok"} if route == "/api/v1/health" else self.get(route)
            if urlsplit(route).path == "/api/v1/construction/permits":
                self.previous_permits(payload)
            return BytesIO(json.dumps(payload).encode())

        with patch("docker.readiness.urlopen", side_effect=open_url), self.assertRaisesRegex(RuntimeError, "contractRevision 2"):
            probe("http://fixture", expected="pinned")

    def test_missing_or_legacy_routes_cannot_hide_behind_a_healthy_old_construction_report(self):
        for target in ("/api/v1/construction/permits", "/api/v1/construction/domrf"):
            def get(route):
                return {} if urlsplit(route).path == target else self.get(route)
            with self.subTest(target=target), self.assertRaises(RuntimeError):
                validate_pages(get)

    def test_revision_marker_alone_cannot_mask_missing_new_permit_fields(self):
        for revision in (None, 1, 2, 3):
            payload = self.previous_permits(copy.deepcopy(self.permits["housing"]))
            if revision is not None:
                payload["contractRevision"] = revision
            with self.subTest(revision=revision), self.assertRaises(RuntimeError):
                validate_permits_page(payload, "housing")

    def test_truthfully_empty_permit_kind_keeps_empty_presentation_and_null_chart(self):
        payload = copy.deepcopy(self.permits["nonresidential"])
        payload["permits"].update(columns=[], rows=[], chart=None, presentation={"rows": []})
        payload["forecast"]["rows"] = []
        for block in payload["breakdowns"]:
            block["rows"] = []
            for series in block["series"]:
                series["points"] = []
        before = copy.deepcopy(payload)
        validate_permits_page(payload, "nonresidential")

        def get(route):
            return payload if route == "/api/v1/construction/permits?permit_kind=nonresidential" else self.get(route)

        validate_pages(get)
        self.assertEqual(payload, before)

    def test_permits_reject_missing_structure_presentation_and_fabricated_annual_basis(self):
        for edit in (
            lambda p: p["selection"].pop("structure"),
            lambda p: p["controls"].pop("structure"),
            lambda p: p["permits"].pop("presentation"),
            lambda p: p["permits"]["presentation"]["rows"][0].update(selected="0"),
            lambda p: p["permits"]["presentation"]["rows"][0].update(annualActual=100, annualComplete=False),
            lambda p: p["forecast"].update(unitBasis="verified"),
            lambda p: p["forecast"]["rows"][0].update(value=float("nan")),
            lambda p: p["breakdowns"][0].pop("aggregateField"),
        ):
            payload = copy.deepcopy(self.permits["housing"])
            edit(payload)
            with self.subTest(edit=edit), self.assertRaises(RuntimeError):
                validate_permits_page(payload, "housing")

    def test_missing_optional_permit_supplements_stay_empty_not_zero_filled(self):
        payload = copy.deepcopy(self.permits["housing"])
        payload["forecast"].update(rows=[], source={"file": None, "sheet": "Прогнозы"})
        payload["breakdowns"] = []
        payload["controls"]["structure"].update(coverage=[], years=[])
        before = copy.deepcopy(payload)
        validate_permits_page(payload, "housing")
        self.assertEqual(payload, before)

    def test_actual_service_without_optional_sheets_needs_no_forecast_values_or_file(self):
        fixture = fixtures.ConstructionPagesTests("test_strict_historical_coverage_missing_supplements_do_not_block_primary")
        fixture.setUp()
        self.addCleanup(fixture.doCleanups)
        fixture.write_book(missing=(fixtures.HOUSING_SHEET, fixtures.HISTORY_SHEET, fixtures.FORECAST_SHEET))
        for kind in ("housing", "nonresidential", "total"):
            payload = fixture.permits.report(kind)
            self.assertEqual(payload["forecast"]["rows"], [])
            self.assertTrue(any("отсутствует" in n for n in payload["forecast"]["notes"]))
            validate_permits_page(payload, kind)
            payload["forecast"]["source"]["file"] = None
            validate_permits_page(payload, kind)

    def test_truthful_snapshot_with_unknown_period_needs_no_synthetic_history(self):
        payload = copy.deepcopy(self.domrf["msk"])
        for block in ("construction", "sales"):
            payload[block]["history"] = []
            payload[block]["period"].update(id=None, label="")
        payload["construction"]["metrics"].update(living=0, total=None)
        payload["sales"]["metrics"].update(sold_pct=120, sold_thousand_m2=None)
        before = copy.deepcopy(payload)
        validate_domrf_page(payload, "msk")
        self.assertEqual(payload, before)

    def test_domrf_rejects_bad_history_provenance_coercion_and_unobserved_current_period(self):
        for edit in (
            lambda p: p["region"].update(id="rf"),
            lambda p: p["construction"].pop("history"),
            lambda p: p["construction"]["history"][0].pop("sourceFile"),
            lambda p: p["construction"]["history"][0].update(sourceFile="unregistered.json"),
            lambda p: p["construction"]["history"][0].update(period="2026-13"),
            lambda p: p["construction"]["history"][0].update(reportDate="2026-02-30"),
            lambda p: p["sales"]["history"].append(copy.deepcopy(p["sales"]["history"][0])),
            lambda p: p["sales"]["history"][0]["metrics"].update(sold_thousand_m2=True),
            lambda p: p["sales"]["metrics"].update(sold_thousand_m2="10"),
            lambda p: p["construction"]["metrics"].update(living=999999),
            lambda p: p["construction"].update(history=[]),
        ):
            payload = copy.deepcopy(self.domrf["msk"])
            edit(payload)
            with self.subTest(edit=edit), self.assertRaises(RuntimeError):
                validate_domrf_page(payload, "msk")

    def test_provider_unavailability_is_explicit_and_independent(self):
        for absent in ({"monitoring"}, {"construction"}, {"rasprod"}, {"erz-top", "erz-cards"}, {"monitoring", "construction"}):
            states = {alias: {"state": "unavailable"} for alias in absent}
            with self.subTest(absent=absent):
                validate_pages(lambda route: self.get(route, absent=absent), source_states=states)
        for absent, target in (({"monitoring"}, "/api/v1/construction/permits"),
                               ({"construction"}, "/api/v1/construction/domrf")):
            def get(route):
                return {} if urlsplit(route).path == target else self.get(route, absent=absent)
            with self.subTest(absent=absent), self.assertRaisesRegex(RuntimeError, "explicitly declare unavailable"):
                validate_pages(get, source_states={alias: {"state": "unavailable"} for alias in absent})


if __name__ == "__main__":
    unittest.main()
