"""Compare operational payloads with execution of the original page, not a reimplementation."""
import ast
from io import BytesIO
from pathlib import Path
import unittest

import pandas as pd
import plotly.graph_objects as go
from openpyxl import load_workbook
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.vvod_operational import MONTHS_RU, housing_ytd_table, nonres_ytd_table, quarter_tree
from backend.operational_routes import build_report, create_service, create_router, export_workbook
from backend.profile_service import FilterUnavailable
from pipeline.data_access import DataAccess, DataContext
from pipeline.operational_calculations import catalog
from pipeline.report_values import records
from scripts.check_apartments_parity import Capture


ROOT = Path(__file__).resolve().parents[2]


class OperationalCapture(Capture):
    def __init__(self, month, exclude_mkd, year, quarter, cumulative):
        super().__init__("msk")
        self.selection = {"Период с начала года": month, "Год": year, "Квартал": quarter,
                          "Состав показателя": "Без нежилых помещений в жилых объектах" if exclude_mkd else "Всё нежильё",
                          "Расчёт периода": "С начала года" if cumulative else "За квартал"}

    def selectbox(self, label, options, **kwargs):
        value = self.selection[label]
        assert value in options
        return value

    def radio(self, label, options, **kwargs):
        return self.selectbox(label, options)


class OperationalTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.context = DataContext(ROOT)
        access = DataAccess(cls.context)
        cls.loaders = ("load_vvod_static", "load_monitoring_2_0", "load_monitoring_operational_history")
        cls.before = tuple(access.data_version(name) for name in cls.loaders)
        cls.data = {name: getattr(access, name)() for name in cls.loaders}
        cls.options = catalog(cls.data)

    def capture(self, month, exclude, year, quarter, cumulative):
        path = ROOT / "app/pages/8_Ввод_недвижимости_оперативные.py"
        tree = ast.parse(path.read_text(encoding="utf-8"))
        tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
        ui = OperationalCapture(month, exclude, year, quarter, cumulative)
        env = {"st": ui, "pd": pd, "go": go,
               "COLORS": {key: "#333333" for key in ("red", "green")},
               "apply_theme": lambda: None, "page_header": lambda *args: None,
               "style_plotly": lambda *args, **kwargs: None,
               "chart_download_button": lambda *args, **kwargs: None,
               "table_download_buttons": lambda *args, **kwargs: None,
               "housing_ytd_table": housing_ytd_table, "nonres_ytd_table": nonres_ytd_table,
               "quarter_tree": quarter_tree,
               **{name: lambda value=value: value for name, value in self.data.items()}}
        exec(compile(tree, str(path), "exec"), env)
        return ui, env

    def test_original_page_parity(self):
        year = self.options["currentYear"]
        rv = self.data["load_monitoring_2_0"]["rv"]
        available = sorted({MONTHS_RU.get(str(value).strip().casefold()) for value in
                            rv.loc[pd.to_numeric(rv["Год ввода по Мосстату"], errors="coerce").eq(year),
                                   "Месяц ввода по Мосстату"]} - {None})
        months = sorted({available[0], self.options["defaultMonth"], available[-1]}) if available else [1, 8, 12]
        for month in months:
            for exclude in (False, True):
                for cumulative in (False, True):
                    with self.subTest(month=month, exclude=exclude, cumulative=cumulative):
                        payload = build_report(self.data, self.options, month=month, exclude_mkd=exclude,
                                               year=year, quarter=2, cumulative=cumulative)
                        ui, env = self.capture(month, exclude, year, 2, cumulative)
                        for table, name, figure in zip(payload["tables"], ("housing", "nonres"), ui.figures):
                            self.assertEqual(table["rows"], records(env[name]))
                            self.assertEqual(table["chart"]["x"], list(figure.data[0].x))
                            for field, trace in (("period", 0), ("remainder", 1), ("totals", 2)):
                                self.assertEqual(table["chart"][field], records(pd.DataFrame({"value": figure.data[trace].y})))
                        self.assertEqual(payload["tree"], env["tree"])
        access = DataAccess(self.context)
        self.assertEqual(self.before, tuple(access.data_version(name) for name in self.loaders))

    def test_filters_and_exports(self):
        with self.assertRaises(FilterUnavailable):
            build_report(self.data, self.options, year=2000)
        service = create_service(self.context, check_interval=0)
        payload = service.report()
        book = load_workbook(BytesIO(export_workbook(payload)))
        self.assertEqual(book["housing"].max_row, len(payload["tables"][0]["rows"]) + 1)
        self.assertEqual(book["Structure"].max_row, len(payload["treeRows"]) + 1)
        copy = service.catalog()
        copy["months"].clear()
        self.assertTrue(service.catalog()["months"])
        app = FastAPI()
        app.include_router(create_router(self.context, lambda function, *args, **kwargs: function(*args), service))
        client = TestClient(app)
        options = client.get("/api/v1/commissioning/operational/catalog").json()
        self.assertEqual([item["id"] for item in options["months"]], list(range(1, 13)))
        december = client.get("/api/v1/commissioning/operational", params={"month": 12})
        self.assertEqual(december.status_code, 200)
        december = december.json()
        self.assertEqual(december["selection"]["month"], 12)
        self.assertEqual(set(december["treeGrowth"]), set(december["tree"]))
        exported = client.get("/api/v1/commissioning/operational/export",
                              params={"month": 12, "required_version": december["version"]})
        self.assertEqual(exported.status_code, 200)
        self.assertEqual(exported.headers["X-Data-Version"], december["version"])
        december_book = load_workbook(BytesIO(exported.content))
        for table in december["tables"]:
            self.assertEqual(december_book[table["id"]].max_row, len(table["rows"]) + 1)
        self.assertEqual(client.get("/api/v1/commissioning/operational/export").status_code, 422)
        self.assertEqual(client.get("/api/v1/commissioning/operational/export", params={"required_version": "old"}).status_code, 409)

    def test_full_calendar_reports_match_existing_helpers_on_real_sources(self):
        vvod = self.data["load_vvod_static"]
        rv = self.data["load_monitoring_2_0"]["rv"]
        history = self.data["load_monitoring_operational_history"]["housing_monthly"]
        before = [frame.copy(deep=True) for frame in (vvod["msk_total"], vvod["msk_nonres"], rv, history)]
        for month in range(1, 13):
            for exclude in (False, True):
                payload = build_report(self.data, self.options, month=month, exclude_mkd=exclude)
                expected = [housing_ytd_table(vvod["msk_total"], rv, history, month),
                            nonres_ytd_table(vvod["msk_nonres"], rv, month, exclude_mkd=exclude, period_from_year=2022)]
                for table, frame in zip(payload["tables"], expected):
                    self.assertEqual(table["rows"], records(frame[frame["Год"].ge(2011)].reset_index(drop=True)))
                    self.assertEqual([row["Год"] for row in table["rows"]], list(range(2011, 2027)))
        for frame, snapshot in zip((vvod["msk_total"], vvod["msk_nonres"], rv, history), before):
            pd.testing.assert_frame_equal(frame, snapshot)
        access = DataAccess(self.context)
        self.assertEqual(self.before, tuple(access.data_version(name) for name in self.loaders))


class HistoricalMonthTests(unittest.TestCase):
    def setUp(self):
        years = list(range(2011, 2026))
        rv = pd.DataFrame([
            {"Год ввода по Мосстату": year, "Месяц ввода по Мосстату": month,
             "Квартал ввода по Мосстату": quarter, "Отрасли": "Жилые объекты", "Подтип объекта": "МКД",
             "category_жилое": housing, "category_нежилое_отдельное": standalone,
             "category_нежилое_в_жилом": embedded, "category_моп": 0, "Общая площадь": housing + standalone + embedded}
            for year, month, quarter, housing, standalone, embedded in [
                (2025, "январь", 1, 1_000_000, 100_000, 200_000),
                (2025, "декабрь", 4, 2_000_000, 300_000, 400_000),
                (2026, "август", 3, 500_000, 200_000, 100_000)]
        ])
        history = pd.DataFrame([{"year": year, "month": month, "value_thousand_m2": value}
                                for year in years if year != 2012
                                for month, value in [(1, 1000), (12, 2000)] if not (year == 2013 and month == 12)])
        self.data = {
            "load_vvod_static": {"msk_total": pd.DataFrame({"year": years, "жильё": [10.0] * 15}),
                                 "msk_nonres": pd.DataFrame({"year": years, "общая": [5.0] * 15, "нежильё": [3.0] * 15})},
            "load_monitoring_2_0": {"rv": rv},
            "load_monitoring_operational_history": {"housing_monthly": history, "source_date": "28.09.2026"},
        }
        self.options = catalog(self.data)

    def test_full_calendar_options_keep_latest_year_default(self):
        self.assertEqual([m["id"] for m in self.options["months"]], list(range(1, 13)))
        self.assertEqual(self.options["months"][-1]["label"], "Январь–декабрь")
        self.assertEqual(self.options["defaultMonth"], 8)
        self.assertEqual(self.options["currentYear"], 2026)
        self.assertEqual(self.options["years"], [2026, 2025])

    def test_december_history_and_partial_current_year_preserve_helpers_and_all_rows(self):
        vvod = self.data["load_vvod_static"]
        rv = self.data["load_monitoring_2_0"]["rv"]
        history = self.data["load_monitoring_operational_history"]["housing_monthly"]
        inputs = [vvod["msk_total"], vvod["msk_nonres"], rv, history]
        before = [frame.copy(deep=True) for frame in inputs]
        for exclude in (False, True):
            payload = build_report(self.data, self.options, month=12, year=2025, quarter=4, exclude_mkd=exclude)
            self.assertEqual(payload["selection"]["month"], 12)
            self.assertEqual(payload["periodLabel"], "Январь–декабрь")
            expected = [housing_ytd_table(vvod["msk_total"], rv, history, 12),
                        nonres_ytd_table(vvod["msk_nonres"], rv, 12, exclude_mkd=exclude, period_from_year=2022)]
            for table, frame in zip(payload["tables"], expected):
                self.assertEqual(table["rows"], records(frame))
                self.assertEqual([row["Год"] for row in table["rows"]], list(range(2011, 2027)))
                self.assertIsNone(table["rows"][-1]["За год, млн м²"])
            housing, nonres = [table["rows"] for table in payload["tables"]]
            self.assertEqual(housing[0]["За выбранный период, млн м²"], 3)
            self.assertIsNone(housing[1]["За выбранный период, млн м²"])
            self.assertEqual(housing[2]["За выбранный период, млн м²"], 1)
            self.assertEqual(housing[-1]["За выбранный период, млн м²"], 0.5)
            self.assertTrue(all(row["За выбранный период, млн м²"] is None for row in nonres if row["Год"] < 2022))
            august = build_report(self.data, self.options, month=8, year=2025, quarter=4, exclude_mkd=exclude)
            for december_table, august_table in zip(payload["tables"], august["tables"]):
                for column in ("Год", "За год, млн м²", "За выбранный период, млн м²"):
                    self.assertEqual(december_table["rows"][-1][column], august_table["rows"][-1][column])
            self.assertEqual(payload["tree"], quarter_tree(rv, 2025, 4))
            self.assertEqual(payload["tree"], august["tree"])
            self.assertTrue(any("не прогнозируются" in note for note in payload["notes"]))
        for original, snapshot in zip(inputs, before):
            pd.testing.assert_frame_equal(original, snapshot)

    def test_all_months_are_valid_missing_current_period_is_not_invented(self):
        for month in range(1, 13):
            payload = build_report(self.data, self.options, month=month)
            if month < 8:
                self.assertTrue(all(row["Год"] != 2026 for table in payload["tables"] for row in table["rows"]))
            else:
                self.assertEqual(payload["tables"][0]["rows"][-1]["За выбранный период, млн м²"], 0.5)
        for invalid in (0, 13):
            with self.assertRaises(FilterUnavailable):
                build_report(self.data, self.options, month=invalid)

    def test_tree_growth_missing_previous_year_or_zero_denominator_is_null(self):
        payload = build_report(self.data, self.options, year=2025, quarter=4)
        self.assertEqual(payload["treeGrowth"], dict.fromkeys(payload["tree"]))
        payload = build_report(self.data, self.options, year=2026, quarter=3)
        self.assertEqual(payload["treeGrowth"], dict.fromkeys(payload["tree"]))
        self.assertEqual(len(payload["treeGrowth"]), 15)

    def test_tree_growth_compares_matching_quarter_or_cumulative_period_without_mutation(self):
        rv = self.data["load_monitoring_2_0"]["rv"]
        previous = rv[rv["Год ввода по Мосстату"].eq(2025)].copy()
        previous["Год ввода по Мосстату"] = 2024
        for column in ["category_жилое", "category_нежилое_отдельное", "category_нежилое_в_жилом", "category_моп", "Общая площадь"]:
            previous[column] *= 0.5
        rv = pd.concat([rv, previous], ignore_index=True)
        self.data["load_monitoring_2_0"]["rv"] = rv
        before = rv.copy(deep=True)
        options = catalog(self.data)
        for cumulative in (False, True):
            payload = build_report(self.data, options, year=2025, quarter=4, cumulative=cumulative)
            current = quarter_tree(rv, 2025, 4, cumulative=cumulative)
            previous_tree = quarter_tree(rv, 2024, 4, cumulative=cumulative)
            self.assertEqual(payload["tree"], current)
            self.assertEqual(set(payload["treeGrowth"]), set(current))
            self.assertEqual(len(payload["treeRows"]), 15)
            self.assertEqual({row["id"]: row["value"] for row in payload["treeRows"]}, current)
            for key, value in current.items():
                if previous_tree[key] == 0:
                    self.assertIsNone(payload["treeGrowth"][key])
                else:
                    self.assertAlmostEqual(payload["treeGrowth"][key], (value - previous_tree[key]) / previous_tree[key] * 100)
                    self.assertAlmostEqual(payload["treeGrowth"][key], 100)
        pd.testing.assert_frame_equal(rv, before)

    def test_tree_growth_real_zero_current_value_can_be_minus_100_percent(self):
        rv = self.data["load_monitoring_2_0"]["rv"]
        previous = rv[rv["Год ввода по Мосстату"].eq(2025)].copy()
        previous["Год ввода по Мосстату"] = 2024
        previous["Квартал ввода по Мосстату"] = 3
        rv = pd.concat([rv, previous], ignore_index=True)
        self.data["load_monitoring_2_0"]["rv"] = rv
        payload = build_report(self.data, catalog(self.data), year=2025, quarter=3)
        self.assertEqual(payload["tree"]["total"], 0)
        expected = quarter_tree(rv, 2024, 3)
        for key in payload["tree"]:
            self.assertEqual(payload["treeGrowth"][key], -100 if expected[key] != 0 else None)


if __name__ == "__main__":
    unittest.main()
