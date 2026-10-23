"""Measured release periods are independent of economics display filters."""
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import pandas as pd
from fastapi.testclient import TestClient

from backend.app import create_app
from backend.economics_service import EconomicsService, LOADERS, release_periods
from backend.profile_service import SourceUnavailable
from pipeline.data_access import DataContext
from pipeline.economics_calculations import (
    MSK, RF, VIEWS, accounts_report, ipc_report, salary_report,
)


def monthly(**changes):
    row = dict(region=MSK, year=2024, month=1, quarter=1, value=100.0,
               view=VIEWS[0], period_type="month", unit="rub", source_file="fixture.xlsx")
    return {**row, **changes}


def annual(**changes):
    row = dict(month=None, quarter=None, period_type="year", metric="vrp_total")
    return monthly(**{**row, **changes})


def period(region=MSK, metric="salary", year=2024, month=1):
    return dict(region=region, metric=metric, year=year, month=month)


class ReleasePeriodsTests(unittest.TestCase):
    def test_salary_zero_is_a_fact_but_null_and_nonfinite_values_are_not(self):
        data = pd.DataFrame([
            monthly(month=4), monthly(month=5, value=0),
            monthly(month=6, value=None), monthly(month=7, value=float("nan")),
            monthly(month=8, value=float("inf")), monthly(month=9, value=-float("inf")),
            monthly(region=RF, value=None),
        ])
        self.assertEqual(release_periods("salary", data), [period(month=5)])

    def test_unknown_values_never_establish_or_advance_a_period(self):
        for family in LOADERS:
            row = (annual() if family == "accounts" else
                   monthly(period_type="month_to_month" if family == "ipc" else "month"))
            unknown = [{**row, "year": 2025, "value": value}
                       for value in (None, pd.NA, "", "..", "unknown", "100", True, False)]
            data = pd.DataFrame([row, *unknown])
            with self.subTest(family=family):
                expected = period(metric="vrp_total" if family == "accounts" else family,
                                  month=None if family == "accounts" else 1)
                self.assertEqual(release_periods(family, data), [expected])
                self.assertEqual(release_periods(family, data.iloc[1:]), [])

    def test_salary_uses_month_or_ytd_primary_views_not_year_or_quarter(self):
        data = pd.DataFrame([
            monthly(month=5), monthly(month=6, period_type="ytd", view=VIEWS[1]),
            monthly(month=12, year=2025, period_type="year"),
            monthly(month=9, year=2025, period_type="quarter"),
            monthly(month=10, year=2025, view="unavailable-view"),
            monthly(month=11, year=2025, period_type="month_to_month"),
            monthly(region=RF, month=8, view=VIEWS[0]),
            monthly(region=RF, month=7, period_type="ytd", view=VIEWS[1]),
        ])
        self.assertEqual(release_periods("salary", data), [period(month=6), period(RF, month=8)])

    def test_latest_is_a_real_year_month_pair_and_regions_are_independent(self):
        data = pd.DataFrame([
            monthly(year=2023, month=12), monthly(year=2025, month=2),
            monthly(year=2024, month=11), monthly(year=2025, month=1),
            monthly(region=RF, year=2024, month=10), monthly(region=RF, year=2023, month=12),
            monthly(year=2025, month=2, view=VIEWS[1]),
        ])
        before = data.copy(deep=True)
        expected = [period(year=2025, month=2), period(RF, year=2024, month=10)]
        self.assertEqual(release_periods("salary", data), expected)
        self.assertEqual(release_periods("salary", data.iloc[::-1]), expected)
        pd.testing.assert_frame_equal(data, before)

    def test_ipc_uses_real_period_type_not_optional_index_base_column(self):
        data = pd.DataFrame([
            monthly(month=2, period_type="month_to_month", index_base="ytd_to_yago"),
            monthly(month=3, period_type="month_to_month", value=0),
            monthly(month=4, period_type="month_to_month", value=None),
            monthly(year=2025, month=12, period_type="ytd_to_yago", index_base="month_to_month"),
            monthly(year=2025, month=12, period_type="year"),
            monthly(year=2025, month=12, period_type="quarter"),
            monthly(year=2025, month=12, period_type="month"),
            monthly(year=2025, month=12, period_type="ytd"),
            monthly(region=RF, month=8, period_type="month_to_month"),
        ])
        expected = [period(metric="ipc", month=3), period(RF, metric="ipc", month=8)]
        self.assertEqual(release_periods("ipc", data), expected)
        self.assertEqual(release_periods("ipc", data.drop(columns="index_base")), expected)

    def test_monthly_missing_invalid_months_or_years_never_fabricate_periods(self):
        for family, kind in (("salary", "month"), ("ipc", "month_to_month")):
            rows = [monthly(period_type=kind)]
            rows += [monthly(year=2025, month=value, period_type=kind)
                     for value in (None, pd.NA, float("nan"), float("inf"), -1, 0, 13, 1.5, "bad", True)]
            rows += [monthly(year=value, month=12, period_type=kind)
                     for value in (None, pd.NA, float("nan"), float("inf"), -1, 0, 10000, 2025.5, "bad", True)]
            rows += [monthly(region=value, year=2025, month=12, period_type=kind)
                     for value in (None, pd.NA, "", " ", 42)]
            with self.subTest(family=family):
                self.assertEqual(release_periods(family, pd.DataFrame(rows)), [period(metric=family)])
                self.assertEqual(release_periods(family, pd.DataFrame(rows[1:])), [])
                self.assertEqual(release_periods(family, pd.DataFrame(rows).drop(columns="month")), [])

    def test_nullable_numeric_columns_and_json_scalar_types(self):
        data = pd.DataFrame([monthly(), monthly(month=2, value=0), monthly(month=None, value=None)])
        data["year"] = data["year"].astype("Int64")
        data["month"] = data["month"].astype("Int64")
        data["value"] = data["value"].astype("Float64")
        result = release_periods("salary", data)
        self.assertEqual(result, [period(month=2)])
        self.assertIs(type(result[0]["year"]), int)
        self.assertIs(type(result[0]["month"]), int)
        self.assertEqual(json.loads(json.dumps(result, allow_nan=False)), result)

    def test_accounts_only_measured_annual_known_metrics_per_region(self):
        data = pd.DataFrame([
            annual(year=2022), annual(year=2023, value=0), annual(year=2024, value=None),
            annual(year=2025, value=float("inf")), annual(year=2026, value=-float("inf")),
            annual(year=2027, period_type="month"), annual(year=2028, period_type="quarter"),
            annual(year=2029, period_type="ytd"), annual(year=2030, metric="unknown"),
            annual(region=RF, metric="gdp_total", year=2024),
            annual(region=RF, metric="gdp_total", year=2025, value=float("nan")),
            annual(region=RF, year=2021),
            annual(metric="vrp_index", year=2022),
        ])
        expected = [period(metric="vrp_index", year=2022, month=None),
                    period(metric="vrp_total", year=2023, month=None),
                    period(RF, metric="gdp_total", year=2024, month=None),
                    period(RF, metric="vrp_total", year=2021, month=None)]
        self.assertEqual(release_periods("accounts", data), expected)
        self.assertEqual(release_periods("accounts", data.drop(columns=["month", "quarter"])), expected)

    def test_accounts_preserve_all_distinct_ui_metrics(self):
        metrics = ["gdp_total", "gdp_pc_total", "gdp_index", "gdp_pc_index", "vrp_total",
                   "vrp_per_capita", "vrp_index", "vrp_per_capita_index", "vds_value",
                   "vds_structure", "vds_index"]
        data = pd.DataFrame([annual(metric=metric, year=2020 + index % 5)
                             for index, metric in enumerate(metrics)])
        expected = sorted([period(metric=metric, year=2020 + index % 5, month=None)
                           for index, metric in enumerate(metrics)], key=lambda item: item["metric"])
        self.assertEqual(release_periods("accounts", data), expected)

    def test_accounts_reject_invalid_years_without_truncating_fractional_year(self):
        data = pd.DataFrame([annual(year=2020.0)] + [annual(year=year) for year in (
            None, pd.NA, float("nan"), float("inf"), -float("inf"), 0, -1, 10000, 2025.5, "bad", True,
        )])
        self.assertEqual(release_periods("accounts", data), [period(metric="vrp_total", year=2020, month=None)])
        self.assertEqual(release_periods("accounts", data.iloc[1:]), [])

    def test_empty_missing_schema_and_no_eligible_facts(self):
        for family in LOADERS:
            data = pd.DataFrame([annual() if family == "accounts" else monthly()])
            with self.subTest(family=family):
                self.assertEqual(release_periods(family, pd.DataFrame()), [])
                self.assertEqual(release_periods(family, data.iloc[:0]), [])
                self.assertEqual(release_periods(family, data.drop(columns="period_type")), [])
                self.assertEqual(release_periods(family, data.assign(value=None)), [])
                self.assertEqual(release_periods(family, data.assign(period_type=None)), [])


class ReleasePeriodsServiceTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.context = DataContext(Path(temporary.name))
        self.data = {
            "salary": pd.DataFrame([
                monthly(), monthly(month=8, quarter=3, view=VIEWS[1], period_type="ytd", value=0),
                monthly(region=RF, month=6, quarter=2),
            ]),
            "ipc": pd.DataFrame([
                monthly(period_type="month_to_month"),
                monthly(month=9, quarter=3, period_type="month_to_month", value=0),
                monthly(region=RF, month=7, quarter=3, period_type="month_to_month"),
                monthly(year=2025, month=12, quarter=4, period_type="ytd_to_yago"),
            ]),
            "accounts": pd.DataFrame([
                annual(year=2023), annual(region=RF, metric="gdp_total", year=2024),
                annual(metric="vds_value", view=VIEWS[0], year=2022),
                annual(metric="vds_structure", view=VIEWS[0], year=2022),
                annual(metric="vds_index", view=VIEWS[0], year=2021),
            ]),
        }
        self.access = Mock(issues=[])
        self.access.data_version.return_value = (("fixture.pkl", 1),)
        for family, loader in LOADERS.items():
            getattr(self.access, loader).side_effect = lambda family=family: self.data[family].copy(deep=True)
        patcher = patch("backend.economics_service.DataAccess", return_value=self.access)
        patcher.start()
        self.addCleanup(patcher.stop)
        self.services = {family: EconomicsService(self.context, family, check_interval=0) for family in LOADERS}

    def test_catalog_and_reports_share_unfiltered_periods_and_unchanged_calculations(self):
        filters = {
            "salary": [dict(region=RF, views=VIEWS[:1], months=[1]),
                       dict(ytd=True, views=VIEWS[1:], months=[1]), dict(period="year"),
                       dict(period="quarter", ytd=True), dict(views=[]), dict(months=[])],
            "ipc": [dict(regions=[RF], months=[1]), dict(index_base="ytd_to_yago", months=[1]),
                    dict(period="year"), dict(period="quarter"), dict(regions=[]), dict(months=[])],
            "accounts": [dict(block1_regions=[RF], structure_region=RF, index_region=RF,
                              structure_mode="share", show_total=False),
                         {**{f"block{i}_regions": [] for i in range(1, 5)},
                          "structure_industries": [], "index_industries": []}],
        }
        builders = {"salary": salary_report, "ipc": ipc_report, "accounts": accounts_report}
        for family, service in self.services.items():
            catalog = service.catalog()
            expected = release_periods(family, self.data[family])
            self.assertTrue(expected)
            self.assertEqual(catalog["releasePeriods"], expected)
            for selection in [{}, *filters[family]]:
                with self.subTest(family=family, filters=selection):
                    report = service.report(**selection)
                    self.assertEqual(report["schemaVersion"], 1)
                    self.assertEqual(report["version"], catalog["version"])
                    self.assertEqual(report["releasePeriods"], expected)
                    original = builders[family](self.data[family], **report["selection"])
                    self.assertEqual({key: report[key] for key in original}, original)

    def test_release_period_lists_and_items_are_defensive_copies(self):
        for service in self.services.values():
            catalog = service.catalog()
            expected = service.report()["releasePeriods"]
            catalog["releasePeriods"][0]["year"] = 1
            report = service.report()
            report["releasePeriods"].clear()
            self.assertEqual(service.catalog()["releasePeriods"], expected)
            self.assertEqual(service.report()["releasePeriods"], expected)

    def test_periods_refresh_atomically_and_do_not_use_source_dates(self):
        service = self.services["salary"]
        before = service.catalog()
        source = self.context.downloads / "fixture.xlsx"
        source.parent.mkdir()
        source.write_bytes(b"provenance fixture")
        changed_source = service.catalog()
        self.assertNotEqual(changed_source["version"], before["version"])
        self.assertIsNotNone(changed_source["source"]["date"])
        self.assertEqual(changed_source["releasePeriods"], before["releasePeriods"])
        self.data["salary"] = pd.concat([
            self.data["salary"], pd.DataFrame([monthly(year=2025, month=2)]),
        ], ignore_index=True)
        self.access.data_version.return_value = (("fixture.pkl", 2),)
        after = service.report(months=[])
        self.assertEqual(after["charts"], [])
        self.assertNotEqual(after["version"], changed_source["version"])
        self.assertEqual(after["releasePeriods"], [period(year=2025, month=2), period(RF, month=6)])
        self.assertEqual(service.catalog()["releasePeriods"], after["releasePeriods"])

    def test_absent_facts_are_empty_but_empty_sources_keep_existing_error_behavior(self):
        for family, service in self.services.items():
            with self.subTest(family=family):
                self.data[family]["value"] = float("nan")
                self.assertEqual(service.catalog()["releasePeriods"], [])
                filters = {"structure_industries": []} if family == "accounts" else {}
                self.assertEqual(service.report(**filters)["releasePeriods"], [])
                self.data[family] = pd.DataFrame()
                self.access.data_version.return_value = (("fixture.pkl", family),)
                with self.assertRaises(SourceUnavailable):
                    service.catalog()
                with self.assertRaises(SourceUnavailable):
                    service.report()

    def test_http_v1_catalog_report_and_alias_expose_additive_field(self):
        client = TestClient(create_app())
        filters = {"salary": {"months": "", "views": ""},
                   "ipc": {"regions": "", "index_base": "ytd_to_yago"},
                   "accounts": {f"block{i}_regions": "" for i in range(1, 5)}}
        for family in LOADERS:
            base = f"/api/v1/economics/{family}"
            catalog = client.get(base + "/catalog")
            self.assertEqual(catalog.status_code, 200, catalog.text)
            expected = release_periods(family, self.data[family])
            self.assertEqual(catalog.json()["schemaVersion"], 1)
            self.assertEqual(catalog.json()["releasePeriods"], expected)
            for suffix in ("", "/report"):
                with self.subTest(family=family, suffix=suffix):
                    response = client.get(base + suffix, params=filters[family])
                    self.assertEqual(response.status_code, 200, response.text)
                    payload = response.json()
                    self.assertEqual(payload["schemaVersion"], 1)
                    self.assertEqual(payload["version"], catalog.json()["version"])
                    self.assertEqual(payload["releasePeriods"], expected)
                    for item in payload["releasePeriods"]:
                        self.assertEqual(set(item), {"region", "metric", "year", "month"})


if __name__ == "__main__":
    unittest.main()
