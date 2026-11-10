from datetime import date
from io import BytesIO
import json
from pathlib import Path
import shutil
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
from openpyxl import Workbook, load_workbook

from app.linear_objects import DSTI_LABELS, DSTI_SHEET, read_linear_objects
from backend.linear_routes import create_router, export_dashboard
from backend.linear_service import LinearService
from backend.profile_service import SourceUnavailable
from pipeline.commissioning_forecasts import FORECAST_FIELDS
from pipeline.data_access import DataContext
from pipeline.linear_dashboard import (HISTORY_SHEET, METRICS, STATIC_DEPOT_HISTORY,
                                       STATIC_DEPOT_SOURCE, build_dashboard, number, read_history)
from tests.monitoring_dsti_fixture import add_monitoring_dsti


class LinearDashboardTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.year = date.today().year
        self.service = LinearService(DataContext(self.root), check_interval=0)
        self.monitoring = self.root / f"data/raw/realty/nashdom/monitoring_2_0_{self.year}1016.xlsx"
        self.dsti = self.root / f"data/raw/realty/linear_objects/linear_objects_{self.year}-09-28.json"

    def write_monitoring(self, horizon=2, history=True, forecast=True):
        self.monitoring.parent.mkdir(parents=True, exist_ok=True)
        book = load_workbook(self.monitoring) if self.monitoring.exists() else Workbook()
        for tab in list(book):
            if tab.title != DSTI_SHEET:
                book.remove(tab)
        if history:
            tab = book.create_sheet(HISTORY_SHEET)
            tab.cell(254, 4, "Ввод линейные объекты")
            tab.cell(255, 4, "Показатель")
            tab.cell(255, 5, "ед. изм")
            for col, year in enumerate([self.year - 2, self.year - 1, self.year, self.year + 1], 6):
                tab.cell(255, col, year)
            for row, (key, label, unit, source_label, code) in enumerate(METRICS, 256):
                if key == "depots":
                    continue
                tab.cell(row, 4, source_label)
                tab.cell(row, 5, unit)
                for col, value in enumerate([20, 30, 0 if key == "metro_stations" else 7, 999], 6):
                    tab.cell(row, col, value)
        if forecast:
            tab = book.create_sheet("Прогнозы")
            for offset in range(horizon + 1):
                tab.cell(2, offset + 4, self.year + offset)
            for row, (key, label) in FORECAST_FIELDS.items():
                tab.cell(row, 3, label)
                for offset in range(horizon + 1):
                    if not (key == "depots" and offset == 0):
                        tab.cell(row, offset + 4, 0 if key == "metro_stations" else 50 + offset)
        if not book.sheetnames:
            book.create_sheet("Unrelated")
        book.save(self.monitoring)
        book.close()

    def write_dsti(self, quarters=4, fact=2, plan=10):
        rows = [{"code": f"1.{code}", "indicator": f"Indicator {code}", "unit": "км" if code == 1 else "ед",
                 "year": year, "quarter": q, "fact": fact if q < 4 else None, "plan": plan}
                for code in range(1, 5) for year in [self.year - 1, self.year, self.year + 1]
                for q in range(1, quarters + 1)]
        self.write_dsti_rows(rows)
        return rows

    def write_dsti_rows(self, rows):
        self.monitoring.parent.mkdir(parents=True, exist_ok=True)
        book = load_workbook(self.monitoring) if self.monitoring.exists() else Workbook()
        if DSTI_SHEET in book.sheetnames:
            book.remove(book[DSTI_SHEET])
        tab = book.create_sheet(DSTI_SHEET)
        for col, text in enumerate(["№", "Показатель", "Единица измерения", "Период расчета"], 1):
            tab.cell(4, col, text)
        periods = sorted({(row["year"], row["quarter"]) for row in rows})
        for offset, (year, quarter) in enumerate(periods):
            for index, kind in enumerate(["факт", "план"]):
                tab.cell(4, 6 + offset * 3 + index, f"{quarter} квартал {year} ({kind})")
        for label, (code, unit) in DSTI_LABELS.items():
            row_index = 4 + int(code[-1])
            tab.cell(row_index, 1, date(self.year, int(code[-1]), 1))
            tab.cell(row_index, 2, label)
            tab.cell(row_index, 3, unit)
            tab.cell(row_index, 4, "за текущий период")
            for row in rows:
                if row["code"] == code:
                    offset = periods.index((row["year"], row["quarter"]))
                    for index, key in enumerate(["fact", "plan"]):
                        tab.cell(row_index, 6 + offset * 3 + index, row[key])
        book.save(self.monitoring)
        book.close()

    def client(self):
        app = FastAPI()
        app.include_router(create_router(DataContext(self.root), lambda fn, *args, **kwargs: fn(*args), self.service))
        return TestClient(app)

    def write_sample_dsti(self):
        self.year = 2026
        self.monitoring = self.root / "data/raw/realty/nashdom/monitoring_2_0_20261110.xlsx"
        self.write_monitoring()
        book = load_workbook(self.monitoring)
        add_monitoring_dsti(book)
        book.save(self.monitoring)
        book.close()

    def test_monitoring_slice_values_sources_cells_and_partial_periods(self):
        self.write_sample_dsti()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 10)
            payload = self.service.dashboard()
        self.assertEqual(payload["calculationRevision"], "monitoring-dsti-sheet-v3")
        for metric, fact, plan, cells in zip(payload["metrics"], [47.1, 16, 12], [74.6, 27, 34],
                                            [["F5", "I5", "L5", "O5"], ["F6", "I6", "L6"], ["F7", "I7", "L7", "O7"]]):
            self.assertAlmostEqual(metric["fact"], fact)
            self.assertAlmostEqual(metric["annualPlan"], plan)
            self.assertEqual(metric["factKind"], "partial")
            self.assertEqual(metric["factSource"], "dsti")
            self.assertEqual(metric["factCells"], cells)
            self.assertEqual(metric["forecast"], 50)
            prior = next(row for row in metric["years"] if row["year"] == 2025)
            self.assertEqual(prior["fact"], 30)
            self.assertEqual(prior["factSource"], "monitoring")
        self.assertIn("1–3", payload["metrics"][1]["factPeriod"])
        self.assertEqual(payload["sources"][0], {"id": "dsti", "file": self.monitoring.relative_to(self.root).as_posix(),
                                                "date": "2026-11-10", "sheet": "ДСТИ"})
        self.assertEqual(payload["metrics"][3]["factCells"], ["H259"])

    def test_legacy_monitoring_without_dsti_uses_history_not_separate_financial_file(self):
        self.write_monitoring()
        self.dsti.parent.mkdir(parents=True)
        self.dsti.write_bytes(b"this invalid legacy source must not be read by dashboard")
        before = self.service.dashboard()
        roads = before["metrics"][0]
        self.assertEqual(roads["fact"], 7)
        self.assertEqual(roads["factSource"], "monitoring")
        self.assertEqual(roads["factCells"], ["H256"])
        self.assertTrue(any("Лист «ДСТИ» отсутствует" in note for note in before["notes"]))
        self.assertTrue(any("Дороги:" in note and "годовой блок" in note for note in before["notes"]))
        self.dsti.unlink()
        self.assertEqual(self.service.dashboard()["version"], before["version"])

    def test_invalid_dsti_has_explicit_history_fallback(self):
        self.write_sample_dsti()
        book = load_workbook(self.monitoring)
        book[DSTI_SHEET]["C5"] = "млн руб."
        book.save(self.monitoring)
        book.close()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 10)
            payload = self.service.dashboard()
        self.assertEqual(payload["metrics"][0]["factSource"], "monitoring")
        self.assertTrue(any("не прошёл проверку" in note for note in payload["notes"]))

    def test_blank_row_falls_back_per_metric_and_never_uses_plan_as_forecast(self):
        self.write_sample_dsti()
        book = load_workbook(self.monitoring)
        for cell in ("F5", "I5", "L5", "O5"):
            book[DSTI_SHEET][cell] = None
        book["Прогнозы"]["D40"] = None
        book.save(self.monitoring)
        book.close()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 10)
            payload = self.service.dashboard()
        roads, bridges = payload["metrics"][:2]
        self.assertEqual(roads["factSource"], "monitoring")
        self.assertEqual(bridges["factSource"], "dsti")
        self.assertAlmostEqual(roads["annualPlan"], 74.6)
        for key in ("forecast", "target", "targetKind", "remaining", "percent"):
            self.assertIsNone(roads[key])

    def test_source_sheet_edit_invalidates_dashboard_and_pinned_export(self):
        self.write_sample_dsti()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 10)
            before = self.service.dashboard()
            book = load_workbook(self.monitoring)
            book[DSTI_SHEET]["F5"] = 11.2
            book.save(self.monitoring)
            book.close()
            after = self.service.dashboard()
        self.assertNotEqual(after["version"], before["version"])
        self.assertAlmostEqual(after["metrics"][0]["fact"] - before["metrics"][0]["fact"], 1)
        self.assertEqual(self.client().get("/api/v1/linear/dashboard/export", params={"required_version": before["version"]}).status_code, 409)

    def test_old_dsti_snapshot_does_not_become_completed_annual_history(self):
        self.write_sample_dsti()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2027, 2, 1)
            payload = self.service.dashboard()
        roads = payload["metrics"][0]
        self.assertIsNone(roads["fact"])
        old = next(row for row in roads["years"] if row["year"] == 2026)
        self.assertIsNone(old["fact"])
        self.assertIsNone(old["factKind"])

    def test_quarter_gap_and_future_quarters_are_not_summed_as_zero_or_annual(self):
        self.write_sample_dsti()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 5, 1)
            roads = self.service.dashboard()["metrics"][0]
            self.assertAlmostEqual(roads["fact"], 21)
            self.assertEqual(roads["factCells"], ["F5", "I5"])
            self.assertEqual(roads["factKind"], "partial")
        book = load_workbook(self.monitoring)
        book[DSTI_SHEET]["I5"] = None
        book.save(self.monitoring)
        book.close()
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 10)
            roads = self.service.dashboard()["metrics"][0]
            self.assertEqual(roads["fact"], 10.2)
            self.assertEqual(roads["factCells"], ["F5"])
            self.assertIn("1–1", roads["factPeriod"])

    def test_corrupt_monitoring_is_source_failure_not_silent_financial_fallback(self):
        self.monitoring.parent.mkdir(parents=True)
        self.monitoring.write_bytes(b"invalid workbook")
        with self.assertRaises(SourceUnavailable):
            self.service.dashboard()

    def test_quarterly_api_prefers_monitoring_and_exports_sheet_provenance(self):
        self.write_sample_dsti()
        catalog = self.service.catalog()
        self.assertEqual(catalog["source"]["sheet"], "ДСТИ")
        self.assertEqual(catalog["years"][0]["defaultQuarter"], 3)
        payload = self.service.report(2026, 4, True)
        roads = next(row for row in payload["summary"] if row["code"] == "1.1")
        self.assertAlmostEqual(roads["fact"], 47.1)
        self.assertIsNone(next(row for row in payload["summary"] if row["code"] == "1.2")["fact"])
        response = self.client().get("/api/v1/linear/export", params={"required_version": payload["version"],
                                                                    "year": 2026, "quarter": 4, "cumulative": True})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content), data_only=True)
        meta = dict(book["Report"].iter_rows(min_row=2, values_only=True))
        self.assertEqual(meta["sourceSheet"], "ДСТИ")
        book.close()

    def test_quarterly_api_ignores_legacy_file_when_monitoring_sheet_is_present(self):
        self.write_sample_dsti()
        before = self.service.catalog()
        self.dsti.parent.mkdir(parents=True)
        self.dsti.write_text("not valid JSON", encoding="utf-8")
        self.assertEqual(self.service.catalog()["version"], before["version"])

    def test_legacy_quarterly_fallback_is_explicit_when_monitoring_loses_sheet(self):
        self.write_sample_dsti()
        before = self.service.catalog()
        self.dsti.parent.mkdir(parents=True)
        source = Path(__file__).resolve().parents[2] / "data/raw/realty/linear_objects/linear_objects_2026-09-28.json"
        shutil.copy2(source, self.dsti)
        book = load_workbook(self.monitoring)
        book.remove(book[DSTI_SHEET])
        book.save(self.monitoring)
        book.close()
        after = self.service.catalog()
        self.assertNotEqual(before["version"], after["version"])
        self.assertIsNone(after["source"]["sheet"])
        self.assertEqual(after["source"]["files"], [self.dsti.relative_to(self.root).as_posix()])
        self.assertTrue(any("старый отдельный источник" in issue for issue in after["source"]["issues"]))

    def test_absent_dynamic_sources_keep_only_agreed_static_depot_history(self):
        payload = self.service.dashboard()
        self.assertEqual([row["id"] for row in payload["metrics"]], [row[0] for row in METRICS])
        for row in payload["metrics"]:
            if row["id"] == "depots":
                self.assertEqual([(year["year"], year["fact"]) for year in row["years"]], list(STATIC_DEPOT_HISTORY))
            else:
                self.assertEqual(row["years"], [])
            for key in ("fact", "annualPlan", "forecast", "target", "percent", "remaining"):
                self.assertIsNone(row[key])
        self.assertTrue(all(row["file"] is None for row in payload["sources"] if row["id"] != "static_depots"))
        self.assertEqual(payload["sources"][-1], STATIC_DEPOT_SOURCE)
        json.dumps(payload, allow_nan=False)

    def test_real_named_history_discovered_axis_zero_and_no_future_facts(self):
        self.write_monitoring()
        payload = self.service.dashboard()
        self.assertEqual(payload["metrics"][0]["years"][0]["year"], self.year - 2)
        station = payload["metrics"][3]
        self.assertEqual(station["fact"], 0)
        self.assertEqual(station["forecast"], 0)
        self.assertIsNone(station["percent"])
        self.assertEqual(station["remaining"], 0)
        self.assertIsNone(payload["metrics"][5]["fact"])
        self.assertEqual(payload["metrics"][5]["years"][0]["fact"], 0)
        self.assertEqual(payload["metrics"][5]["years"][0]["factSource"], "static_depots")
        for metric in payload["metrics"]:
            for row in metric["years"]:
                if row["year"] > self.year:
                    self.assertIsNone(row["fact"])
                    self.assertIsNone(row["factKind"])
                if row["year"] == self.year and row["fact"] is not None:
                    self.assertEqual(row["factKind"], "partial")
                    self.assertIn("неполный", row["factPeriod"])

    def test_dsti_partial_not_annual_no_missing_quarter_plan(self):
        self.write_dsti(quarters=3)
        payload = self.service.dashboard()
        roads = payload["metrics"][0]
        self.assertEqual(roads["fact"], min(3, (date.today().month - 1) // 3 + 1) * 2)
        self.assertIsNone(roads["annualPlan"])
        self.assertIsNone(roads["percent"])
        self.assertIsNone(roads["years"][0]["fact"])
        self.assertTrue(all(row["year"] <= self.year for row in roads["years"]))

    def test_dsti_complete_plan_zero_fact_and_current_dsti_precedence(self):
        self.write_dsti(fact=0, plan=0)
        roads = self.service.dashboard()["metrics"][0]
        self.assertEqual(roads["annualPlan"], 0)
        self.assertEqual(roads["fact"], 0)
        self.assertIsNone(roads["remaining"])
        self.assertIsNone(roads["percent"])
        self.write_monitoring()
        roads = self.service.dashboard()["metrics"][0]
        self.assertEqual(roads["fact"], 0)
        self.assertEqual(roads["factSource"], "dsti")
        self.assertEqual(roads["annualPlan"], 0)
        self.assertEqual(roads["forecast"], 50)
        self.assertEqual(roads["targetKind"], "forecast")
        self.assertEqual(roads["target"], 50)
        self.assertAlmostEqual(roads["percent"], 0)
        self.assertEqual(roads["remaining"], 50)

    def test_separate_financial_snapshot_is_ignored_by_dashboard(self):
        self.year = 2026
        self.monitoring = self.root / "data/raw/realty/nashdom/monitoring_2_0_20261016.xlsx"
        self.dsti = self.root / "data/raw/realty/linear_objects/linear_objects_2026-09-28.json"
        self.write_monitoring()
        self.write_dsti()
        source = Path(__file__).resolve().parents[2] / "data/raw/realty/linear_objects/linear_objects_2026-11-05.json"
        self.dsti.parent.mkdir(parents=True)
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 9)
            before = self.service.dashboard()
            shutil.copy2(source, self.dsti.parent / source.name)
            response = self.client().get("/api/v1/linear/dashboard")
            self.assertEqual(response.status_code, 200)
            after = response.json()
            self.assertEqual(after["version"], before["version"])
            self.assertEqual(after["sources"][0]["date"], "2026-10-16")
            self.assertEqual(after["sources"][0]["sheet"], DSTI_SHEET)
            for index in range(3):
                metric = after["metrics"][index]
                self.assertAlmostEqual(metric["fact"], 6)
                self.assertEqual(metric["factSource"], "dsti")
                self.assertIn("1–3", metric["factPeriod"])
                self.assertIn("неполный год", metric["factPeriod"])
                self.assertEqual(metric["forecast"], before["metrics"][index]["forecast"])
                self.assertEqual(metric["target"], metric["forecast"])
                self.assertAlmostEqual(metric["percent"], 6 / metric["forecast"] * 100)
                self.assertAlmostEqual(metric["remaining"], metric["forecast"] - 6)
                def history_values(item):
                    return [(row["year"], row["fact"], row["factKind"], row["factSource"])
                            for row in item["years"] if row["year"] < 2026]
                self.assertEqual(history_values(metric), history_values(before["metrics"][index]))
                self.assertEqual([(row["year"], row["forecast"]) for row in metric["years"]],
                                 [(row["year"], row["forecast"]) for row in before["metrics"][index]["years"]])
                current = next(row for row in metric["years"] if row["year"] == 2026)
                self.assertEqual(current["factKind"], "partial")
            self.assertEqual(after["metrics"][3:], before["metrics"][3:])
            # A newer workbook does not prove a newer observation in its annual block.
            self.monitoring.rename(self.monitoring.with_name("monitoring_2_0_20261106.xlsx"))
            later = self.service.dashboard()["metrics"][0]
            self.assertEqual(later["factSource"], "dsti")
            self.assertAlmostEqual(later["fact"], 6)

    def test_monitoring_dsti_overrides_annual_block_in_current_year_and_export(self):
        self.year = 2026
        self.monitoring = self.root / "data/raw/realty/nashdom/monitoring_2_0_20261109.xlsx"
        self.dsti = self.root / "data/raw/realty/linear_objects/linear_objects_2026-11-05.json"
        self.write_monitoring()
        book = load_workbook(self.monitoring)
        for row, value in ((256, 45.6), (257, 15), (258, 9)):
            book[HISTORY_SHEET].cell(row, 8, value)
        book.save(self.monitoring)
        book.close()
        source = Path(__file__).resolve().parents[2] / "data/raw/realty/linear_objects" / self.dsti.name
        data = read_linear_objects(source)
        rows = data.astype(object).where(data.notna(), None).to_dict("records")
        self.write_dsti_rows(rows)
        with patch("backend.linear_service.date") as clock:
            clock.today.return_value = date(2026, 11, 9)
            response = self.client().get("/api/v1/linear/dashboard")
            self.assertEqual(response.status_code, 200)
            payload = response.json()
            self.assertEqual(payload["sources"][0]["date"], "2026-11-09")
            self.assertEqual(payload["sources"][1]["date"], "2026-11-09")
            self.assertEqual(payload["sources"][0]["file"], payload["sources"][1]["file"])
            for index, fact in enumerate((47.1, 16, 12)):
                metric = payload["metrics"][index]
                self.assertAlmostEqual(metric["fact"], fact)
                self.assertEqual(metric["factSource"], "dsti")
                current = next(row for row in metric["years"] if row["year"] == 2026)
                self.assertAlmostEqual(current["fact"], fact)
                self.assertEqual(current["factSource"], "dsti")
                self.assertEqual(current["factKind"], "partial")
                historical = next(row for row in metric["years"] if row["year"] == 2025)
                self.assertEqual(historical["fact"], 30)
                self.assertEqual(historical["factSource"], "monitoring")
                self.assertEqual(metric["target"], 50)
                self.assertAlmostEqual(metric["percent"], fact / 50 * 100)
                self.assertAlmostEqual(metric["remaining"], 50 - fact)
            self.assertEqual(payload["metrics"][3]["factSource"], "monitoring")
            exported = self.client().get("/api/v1/linear/dashboard/export", params={"required_version": payload["version"]})
            self.assertEqual(exported.status_code, 200)
            book = load_workbook(BytesIO(exported.content), data_only=True)
            rows = list(book["Current year"].values)
            road = dict(zip(rows[0], rows[1]))
            self.assertAlmostEqual(road["fact"], 47.1)
            self.assertEqual(road["factSource"], "dsti")
            self.assertEqual(road["factCells"], "F5, I5, L5, O5")
            annual = list(book["Yearly data"].values)
            road = next(dict(zip(annual[0], row)) for row in annual[1:] if row[0] == "roads" and row[3] == 2026)
            self.assertAlmostEqual(road["fact"], 47.1)
            self.assertEqual(road["factSource"], "dsti")
            book.close()

    def test_missing_current_dsti_observation_falls_back_to_monitoring(self):
        self.write_monitoring()
        self.write_dsti(fact=None)
        roads = self.service.dashboard()["metrics"][0]
        self.assertEqual(roads["fact"], 7)
        self.assertEqual(roads["factSource"], "monitoring")
        self.assertEqual(roads["target"], 50)
        self.assertAlmostEqual(roads["percent"], 14)
        self.assertEqual(roads["remaining"], 43)

    def test_calculation_revision_changes_dashboard_and_export_version_without_source_mutation(self):
        self.write_monitoring()
        self.write_dsti()
        before = self.service.dashboard()
        with patch("backend.linear_service.LINEAR_CALCULATION_REVISION", "old-priority"):
            after = self.service.dashboard()
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual(before["metrics"], after["metrics"])
        self.assertEqual(self.client().get("/api/v1/linear/dashboard/export", params={"required_version": after["version"]}).status_code, 409)

    def test_only_forecast_defines_target_with_missing_zero_or_exceeded_values(self):
        rows = self.write_dsti(fact=2, plan=100)
        roads = self.service.dashboard()["metrics"][0]
        self.assertEqual(roads["annualPlan"], 400)
        for key in ("target", "targetKind", "percent", "remaining"):
            self.assertIsNone(roads[key])
        self.write_monitoring()
        for row in rows:
            if row["code"] == "1.1" and row["year"] == self.year:
                row["fact"] = 70 if row["quarter"] == 1 else 0
        self.write_dsti_rows(rows)
        roads = self.service.dashboard()["metrics"][0]
        self.assertEqual(roads["target"], 50)
        self.assertEqual(roads["percent"], 140)
        self.assertEqual(roads["remaining"], 0)
        book = load_workbook(self.monitoring)
        book["Прогнозы"].cell(40, 4, 0)
        book.save(self.monitoring)
        book.close()
        roads = self.service.dashboard()["metrics"][0]
        self.assertEqual(roads["target"], 0)
        self.assertIsNone(roads["percent"])
        self.assertEqual(roads["remaining"], 0)

    def test_complete_historical_dsti_only_when_all_four_measured(self):
        rows = self.write_dsti()
        for row in rows:
            if row["year"] < self.year:
                row["fact"] = 1
        self.write_dsti_rows(rows)
        historic = self.service.dashboard()["metrics"][0]["years"][0]
        self.assertEqual(historic["fact"], 4)
        self.assertEqual(historic["factKind"], "annual")

    def test_completed_history_still_uses_newer_monitoring_without_changing_current_dsti(self):
        rows = self.write_dsti()
        for row in rows:
            if row["year"] < self.year:
                row["fact"] = 1
        self.write_dsti_rows(rows)
        self.write_monitoring()
        roads = self.service.dashboard()["metrics"][0]
        historic = next(row for row in roads["years"] if row["year"] == self.year - 1)
        self.assertEqual(historic["fact"], 30)
        self.assertEqual(historic["factSource"], "monitoring")
        self.assertEqual(historic["factKind"], "annual")
        self.assertEqual(roads["factSource"], "dsti")
        self.assertEqual(roads["forecast"], 50)

    def test_forecast_horizons_change_version_and_source_removal(self):
        self.write_monitoring(horizon=1)
        before = self.service.dashboard()
        self.write_monitoring(horizon=4)
        after = self.service.dashboard()
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual(after["metrics"][0]["years"][-1]["year"], self.year + 4)
        self.monitoring.unlink()
        self.assertEqual(self.service.dashboard()["metrics"][0]["years"], [])

    def test_forecast_axis_uses_each_metric_horizon_and_retains_zero(self):
        self.write_monitoring(horizon=9)
        book = load_workbook(self.monitoring)
        forecasts = book["Прогнозы"]
        for offset in range(10):
            if offset > 4:
                forecasts.cell(40, offset + 4).value = None
            if offset > 2:
                forecasts.cell(41, offset + 4).value = None
            forecasts.cell(43, offset + 4).value = None
            forecasts.cell(42, offset + 4).value = 0 if offset == 2 else None
        book.save(self.monitoring)
        book.close()
        metrics = {metric["id"]: metric for metric in self.service.dashboard()["metrics"]}
        self.assertEqual(metrics["roads"]["years"][-1]["year"], self.year + 4)
        self.assertEqual(metrics["bridges"]["years"][-1]["year"], self.year + 2)
        self.assertEqual(metrics["metro_stations"]["years"][-1]["year"], self.year + 9)
        self.assertTrue(all(row["forecast"] == 0 for row in metrics["metro_stations"]["years"] if row["year"] >= self.year))
        self.assertEqual([row["year"] for row in metrics["crossings"]["years"]],
                         [self.year - 2, self.year - 1, self.year])
        self.assertEqual([(row["year"], row["forecast"]) for row in metrics["depots"]["years"]],
                         [(year, None) for year, _ in STATIC_DEPOT_HISTORY] + [(self.year + 2, 0)])

    def test_static_depots_exact_counts_with_zeros_and_annual_provenance(self):
        self.assertEqual(STATIC_DEPOT_HISTORY, tuple(zip(range(2011, 2026),
                         (0, 0, 0, 1, 0, 1, 0, 2, 1, 0, 0, 0, 1, 1, 2))))
        depot = self.service.dashboard()["metrics"][5]
        self.assertEqual(sum(row["fact"] for row in depot["years"]), 9)
        for row in depot["years"]:
            self.assertEqual(row["factKind"], "annual")
            self.assertEqual(row["factPeriod"], f"За {row['year']} год")
            self.assertEqual(row["factSource"], "static_depots")
            self.assertIsNone(row["forecast"])

    def test_static_depot_counts_override_only_covered_past_years(self):
        sources = [{"id": "dsti", "date": None}, {"id": "monitoring", "date": "2026-10-01"}]
        history = [{"id": "depots", "year": 2025, "value": 999},
                   {"id": "depots", "year": 2026, "value": 3},
                   {"id": "roads", "year": 2025, "value": 41}]
        report = build_dashboard(None, history, {"rows": [{"year": 2026, "depots": 5}]}, sources,
                                 date(2026, 10, 23), static_depots=STATIC_DEPOT_HISTORY)
        depots = report["metrics"][5]
        self.assertEqual(next(row for row in depots["years"] if row["year"] == 2025)["fact"], 2)
        self.assertEqual(depots["fact"], 3)
        self.assertEqual(depots["factSource"], "monitoring")
        self.assertEqual(depots["forecast"], 5)
        self.assertEqual(depots["percent"], 60)
        self.assertEqual(depots["remaining"], 2)
        self.assertEqual(report["metrics"][0]["years"][0]["fact"], 41)

    def test_static_values_change_version_without_changing_mutable_sources(self):
        before = self.service.dashboard()
        changed = (*STATIC_DEPOT_HISTORY[:-1], (2025, 3))
        with patch("backend.linear_service.STATIC_DEPOT_HISTORY", changed):
            after = self.service.dashboard()
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual(after["metrics"][5]["years"][-1]["fact"], 3)
        self.assertEqual(self.service.dashboard()["metrics"][5]["years"][-1]["fact"], 2)

    def test_static_depots_exported_with_source_and_no_current_year_fabrication(self):
        payload = self.service.dashboard()
        response = self.client().get("/api/v1/linear/dashboard/export", params={"required_version": payload["version"]})
        self.assertEqual(response.status_code, 200)
        book = load_workbook(BytesIO(response.content), data_only=True)
        rows = [row for row in book["Yearly data"].iter_rows(min_row=2, values_only=True) if row[0] == "depots"]
        self.assertEqual([(row[3], row[4]) for row in rows], list(STATIC_DEPOT_HISTORY))
        self.assertTrue(all(row[5] == "annual" and row[7] == "static_depots" for row in rows))
        self.assertEqual(list(book["Sources"].values)[-1], tuple(STATIC_DEPOT_SOURCE.values()))
        self.assertIsNone(payload["metrics"][5]["fact"])
        book.close()

    def test_missing_sheets_do_not_fallback_to_demo(self):
        self.write_monitoring(history=False, forecast=False)
        payload = self.service.dashboard()
        self.assertTrue(all(metric["fact"] is None and metric["forecast"] is None for metric in payload["metrics"]))
        self.assertTrue(payload["notes"])

    def test_version_pinned_export_and_complete_yearly_and_forecast_sheets(self):
        self.write_monitoring(horizon=3)
        payload = self.service.dashboard()
        client = self.client()
        self.assertEqual(client.get("/api/v1/linear/dashboard").status_code, 200)
        self.assertEqual(client.get("/api/v1/linear/dashboard/export").status_code, 422)
        self.assertEqual(client.get("/api/v1/linear/dashboard/export", params={"required_version": "old"}).status_code, 409)
        response = client.get("/api/v1/linear/dashboard/export", params={"required_version": payload["version"]})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["x-data-version"], payload["version"])
        book = load_workbook(BytesIO(response.content))
        self.assertEqual(book["Yearly data"].max_row, 1 + sum(len(metric["years"]) for metric in payload["metrics"]))
        self.assertEqual(book["Forecasts"].max_row, 24)
        self.assertEqual(book["Current year"].max_row, 7)
        for sheet in ("Current year", "Yearly data"):
            self.assertNotIn("annualPlan", [cell.value for cell in book[sheet][1]])
            self.assertNotIn("plan", [cell.value for cell in book[sheet][1]])
        self.write_monitoring(horizon=1)
        self.assertEqual(client.get("/api/v1/linear/dashboard/export", params={"required_version": payload["version"]}).status_code, 409)

    def test_snapshot_immutable_and_export_formula_safety(self):
        payload = self.service.dashboard()
        payload["notes"].append("=1+2")
        book = load_workbook(BytesIO(export_dashboard(payload)))
        self.assertEqual(book["Notes"].cell(book["Notes"].max_row, 1).value, "'=1+2")
        payload["metrics"].clear()
        self.assertEqual(len(self.service.dashboard()["metrics"]), 6)

    def test_source_replacement_while_loading_is_not_published(self):
        self.write_monitoring()
        original = self.service._dashboard_inputs
        calls = 0
        def inputs(access):
            nonlocal calls
            calls += 1
            if calls == 2:
                self.write_monitoring(horizon=4)
            return original(access)
        with patch.object(self.service, "_dashboard_inputs", side_effect=inputs):
            with self.assertRaises(SourceUnavailable):
                self.service.dashboard()
        self.assertIsNone(self.service._dashboard)

    def test_reader_ignores_other_blocks_and_rejects_ambiguous_or_units(self):
        book = Workbook()
        tab = book.active
        tab.title = HISTORY_SHEET
        tab.append(["Ввод жилья", 2011, 888])
        self.assertEqual(read_history(book)[0], [])
        tab.cell(10, 4, "Ввод линейные объекты")
        tab.cell(11, 4, "Показатель")
        tab.cell(11, 6, 2020)
        tab.cell(12, 4, "дороги")
        tab.cell(12, 5, "тыс. м²")
        with self.assertRaises(ValueError):
            read_history(book)
        tab.cell(12, 5, "км")
        tab.cell(30, 4, "Ввод линейные объекты")
        with self.assertRaises(ValueError):
            read_history(book)

    def test_old_partial_snapshot_does_not_become_annual_and_number_nulls(self):
        sources = [{"id": "dsti", "date": None}, {"id": "monitoring", "date": "2024-10-01"}]
        payload = build_dashboard(None, [{"id": "roads", "year": 2024, "value": 10}], {"rows": []}, sources, date(2026, 10, 21))
        self.assertIsNone(payload["metrics"][0]["years"][0]["fact"])
        for value in (None, True, float("nan"), float("inf"), "<1", -1):
            self.assertIsNone(number(value))
        self.assertEqual(number(0), 0)


if __name__ == "__main__":
    unittest.main()
