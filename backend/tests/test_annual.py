"""Real-source parity with execution of all original annual charts and exports."""
import ast
import copy
from io import BytesIO
from pathlib import Path
import unittest

import pandas as pd
import plotly.graph_objects as go
from openpyxl import load_workbook

from backend.annual_routes import LOADERS, catalog, create_service, export_workbook, report
from pipeline.annual_calculations import annual_charts, annual_fact_period, annual_forecast
from pipeline.data_access import DataAccess, DataContext, monitoring_by_year, month_label, quarter_label
from pipeline.report_values import records
from scripts.check_apartments_parity import Capture

ROOT = Path(__file__).resolve().parents[2]


class StopPage(Exception):
    pass


class AnnualCapture(Capture):
    def stop(self):
        raise StopPage()


class AnnualTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = DataContext(ROOT)
        access = DataAccess(cls.context)
        cls.before = tuple(access.data_version(name) for name in LOADERS)
        cls.data = {name: getattr(access, name)() for name in LOADERS}

    def capture(self, region):
        path = ROOT / "app/pages/8_Ввод_недвижимости.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        ui = AnnualCapture(region)
        env = {"st": ui, "pd": pd, "go": go,
               "COLORS": {key: "#333333" for key in ("red", "green", "blue", "amber", "teal", "ink")},
               "apply_theme": lambda: None, "page_header": lambda *args: None,
               "style_plotly": lambda *args, **kwargs: None, "chart_download_button": lambda *args, **kwargs: None,
               "table_download_buttons": lambda frame, **kwargs: ui.exports.append(frame.copy()),
               "monitoring_by_year": monitoring_by_year, "month_label": month_label, "quarter_label": quarter_label,
               **{name: lambda value=value: value for name, value in self.data.items()}}
        try:
            exec(compile(tree, str(path), "exec"), env)
        except StopPage:
            pass
        return ui

    def test_all_legacy_charts_export_rows_and_summary_values(self):
        for region, count in (("Москва", 7), ("РФ", 2)):
            with self.subTest(region=region):
                ui = self.capture(region)
                charts = annual_charts(self.data, region)
                self.assertEqual(len(charts), count)
                self.assertEqual(len(ui.figures), count)
                self.assertEqual(len(ui.exports), count)
                for chart, figure, frame in zip(charts, ui.figures, ui.exports):
                    # Explicit categories replace the legacy total-minus-IHS
                    # split; their source/null rules have dedicated tests.
                    if chart["id"] == "b2_1":
                        self.assertEqual(chart["controlTotal"], "TOTAL")
                        self.assertNotIn("TOTAL", [series["id"] for series in chart["series"]])
                        self.assertTrue(chart["rawCategoryValues"])
                        continue
                    bars = [trace for trace in figure.data if trace.type == "bar"]
                    self.assertEqual([row["name"] for row in chart["series"]], [trace.name for trace in bars])
                    for series, trace in zip(chart["series"], bars):
                        self.assertEqual([p["x"] for p in series["points"]], list(trace.x))
                        expected = [None if region == "РФ" and point["x"] == "2026" else value
                                    for point, value in zip(series["points"], trace.y)]
                        self.assertEqual([p["y"] for p in series["points"]], expected)
                    adapted = [{"year": row["year"], **{col["label"]: row[col["id"]] for col in chart["columns"][1:]}}
                               for row in chart["rows"]]
                    expected_rows = records(frame)
                    if region == "РФ":
                        expected_rows[-1] = {key: value if key == "year" else None for key, value in expected_rows[-1].items()}
                    self.assertEqual(adapted, expected_rows)
                    for summary in chart["summaries"]:
                        subset = frame[frame["year"].between(summary["from"], summary["to"])]
                        for value in summary["values"]:
                            if region == "РФ" and summary["to"] == 2026:
                                self.assertIsNone(value["value"])
                            else:
                                self.assertAlmostEqual(value["value"], float(subset[value["label"]].sum()), places=9)
        access = DataAccess(self.context)
        self.assertEqual(self.before, tuple(access.data_version(name) for name in LOADERS))

    def test_full_workbook_notes_units_and_region_scope(self):
        service = create_service(self.context, check_interval=0)
        msk, rf = service.report(region="msk"), service.report(region="rf")
        self.assertEqual(len(msk["charts"]), 7)
        self.assertEqual(len(rf["charts"]), 2)
        self.assertTrue(any("нет годового итога" in note for note in rf["notes"]))
        book = load_workbook(BytesIO(export_workbook(msk)))
        self.assertEqual(len(book.sheetnames), 15 + sum(bool(chart.get("forecast")) for chart in msk["charts"]))
        self.assertNotIn("forecastSource", rf)
        self.assertTrue(all("forecast" not in chart for chart in rf["charts"]))
        for chart in msk["charts"]:
            self.assertEqual(chart["unit"], "млн м²")
            self.assertEqual(book[chart["id"]].max_row, len(chart["rows"]) + 1)


class AnnualForecastTests(unittest.TestCase):
    def setUp(self):
        years = list(range(2011, 2026))
        housing = pd.DataFrame({"year": years, "жильё": [3.0, None] + [4.0] * 13, "нежильё": [2.0] * 15})
        nonres = pd.DataFrame({"year": years, "нежильё": [2.0] * 15, "нежилые_в_жилье": [.5] * 15})
        budget = pd.DataFrame({"year": years, "Бюджет": [1.0] * 15, "Небюджет": [3.0] * 15})
        rv = pd.DataFrame([{"Год ввода по Мосстату": year, "Месяц ввода по Мосстату": month,
                            "Группировка": "Жилье", "Отрасли": "Жилые объекты", "Подтип объекта": "МКД",
                            "Источник финансирования": "Внебюджет", "Инвестор для Реновации": "да",
                            "Жилая площадь": 1_000_000, "category_жилое": 1_000_000,
                            "category_моп": 500_000, "category_нежилое_отдельное": 200_000,
                            "category_нежилое_в_жилом": 100_000}
                           for year, month in [(2025, "декабрь"), (2026, "сентябрь"), (2027, "декабрь")]])
        self.data = {"load_vvod_static": {"msk_total": housing, "rf_total": housing.copy(),
                      "msk_residential": pd.DataFrame({"year": years, "МКД": [3.0] * 15, "ИЖС": [1.0] * 15}),
                      "msk_nonres": nonres, "msk_nonres_branches": pd.DataFrame({"year": years, "офисы": [1.0] * 15})},
                     "load_emiss_34118": pd.DataFrame(columns=["year"]), "load_emiss_34118_periods": pd.DataFrame(),
                     "load_monitoring_2_0": {"rv": rv},
                     "load_monitoring_2011_2026_static": {"residential_budget_split": budget, "nonres_budget_split": budget.copy()},
                     "load_commissioning_forecasts": {"source_file": "monitoring_2_0_20261021.xlsx", "source_date": "2026-10-21",
                         "unitBasis": "assumed_thousand_m2", "notes": ["Площадь принята в тыс. м²."], "rows": [
                             {"year": 2025, "housing_total": 999999},
                             {"year": 2026, "housing_living": 6000, "housing_total": 10000, "renovation_living": 2500,
                              "nonres_standalone": 4000, "nonres_embedded": 2000, "nonres_total": 6000, "offices": 123456},
                             {"year": 2027, "housing_living": 0, "housing_total": None, "nonres_standalone": 0},
                             {"year": 2029, "roads": 100},
                             {"year": 2031, "housing_living": 8000, "housing_total": 12000, "nonres_standalone": 5000}]}}

    def test_only_matching_moscow_totals_convert_units_and_preserve_all_facts(self):
        before = annual_charts(self.data, "Москва")
        source = copy.deepcopy(self.data["load_commissioning_forecasts"])
        payload = report(self.data, catalog(self.data))
        expected = {"b1": 14.0, "b2_1": 6.0, "b2_2": 6.0, "b2_3": 2.5, "b3_1": 6.0, "b3_2": 4.0, "b3_3": 4.0}
        for original, chart in zip(before, payload["charts"]):
            self.assertEqual({key: value for key, value in chart.items() if key not in ("factPeriod", "forecast")}, original)
            self.assertEqual(chart["factPeriod"], {"year": 2026, "month": 9})
            self.assertEqual(chart["forecast"]["points"][0], {"year": 2026, "value": expected[chart["id"]]})
            self.assertNotIn("offices", chart["forecast"]["sourceFields"])
            self.assertNotIn(2029, [point["year"] for point in chart["forecast"]["points"]])
            self.assertNotIn(2025, [point["year"] for point in chart["forecast"]["points"]])
        charts = {chart["id"]: chart for chart in payload["charts"]}
        self.assertEqual([point["year"] for point in charts["b1"]["forecast"]["points"]], [2026, 2031])
        self.assertEqual(charts["b2_1"]["forecast"]["points"][1], {"year": 2027, "value": 0.0})
        self.assertIsNone(charts["b1"]["summaries"][0]["values"][0]["value"])
        self.assertEqual(payload["forecastSource"]["unitBasis"], "assumed_thousand_m2")
        self.assertEqual(self.data["load_commissioning_forecasts"], source)
        rf = report(self.data, catalog(self.data), region="rf")
        self.assertNotIn("forecastSource", rf)
        self.assertTrue(all("forecast" not in chart for chart in rf["charts"]))
        self.assertIsNone(rf["charts"][0]["rows"][-1]["жильё"])

    def test_no_interpolation_decomposition_or_unmatched_only_bars(self):
        for rows in ([], [{"year": 2026, "roads": 5}], [{"year": 2026, "housing_total": 10000}],
                     [{"year": 2026, "housing_total": 10000, "nonres_standalone": None}]):
            self.data["load_commissioning_forecasts"]["rows"] = rows
            payload = report(self.data, catalog(self.data))
            self.assertTrue(all("forecast" not in chart for chart in payload["charts"]))
        self.assertIsNone(annual_forecast("unmatched", {"rows": [{"year": 2026, "housing_living": 4000}]}, 2026))
        for value in (None, float("nan"), float("inf"), -10, True):
            self.assertIsNone(annual_forecast("b2_1", {"rows": [{"year": 2026, "housing_living": value}]}, 2026))

    def test_fact_period_uses_rv_year_month_not_candidate_or_forecast_dates(self):
        self.assertEqual(annual_fact_period(self.data, 2026, "Москва"), {"year": 2026, "month": 9})
        self.assertEqual(annual_fact_period(self.data, 2026, "РФ"), {"year": 2026, "month": None})
        self.data["load_monitoring_2_0"]["rv"]["Месяц ввода по Мосстату"] = self.data["load_monitoring_2_0"]["rv"]["Месяц ввода по Мосстату"].astype(object)
        self.data["load_monitoring_2_0"]["rv"].loc[1, "Месяц ввода по Мосстату"] = 8
        self.assertEqual(annual_fact_period(self.data, 2026, "Москва")["month"], 8)
        self.data["load_monitoring_2_0"]["rv"].loc[1, "Месяц ввода по Мосстату"] = "неизвестно"
        self.assertIsNone(annual_fact_period(self.data, 2026, "Москва")["month"])

    def test_excel_keeps_forecasts_out_of_fact_and_summary_sheets(self):
        payload = dict(version="test", generatedAt="test", source={}, **report(self.data, catalog(self.data)))
        book = load_workbook(BytesIO(export_workbook(payload)))
        for chart in payload["charts"]:
            rows = list(book[chart["id"]].values)
            self.assertEqual(rows[1:], [tuple(row[column["id"]] for column in chart["columns"]) for row in chart["rows"]])
            forecast_rows = list(book[chart["id"] + "_forecast"].values)
            self.assertEqual([row[-1] for row in forecast_rows[1:]], [point["value"] for point in chart["forecast"]["points"]])
            self.assertTrue(all(row[1] == "Прогноз" for row in forecast_rows[1:]))
        self.assertIn("load_commissioning_forecasts", LOADERS)
        self.assertIn("commissioning_forecasts", create_service(DataContext(ROOT)).families)


if __name__ == "__main__":
    unittest.main()
