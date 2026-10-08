"""Real-source parity with execution of all original annual charts and exports."""
import ast
from io import BytesIO
from pathlib import Path
import unittest

import pandas as pd
import plotly.graph_objects as go
from openpyxl import load_workbook

from backend.annual_routes import LOADERS, create_service, export_workbook
from pipeline.annual_calculations import annual_charts
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
                    bars = [trace for trace in figure.data if trace.type == "bar"]
                    self.assertEqual([row["name"] for row in chart["series"]], [trace.name for trace in bars])
                    for series, trace in zip(chart["series"], bars):
                        self.assertEqual([p["x"] for p in series["points"]], list(trace.x))
                        self.assertEqual([p["y"] for p in series["points"]], list(trace.y))
                    adapted = [{"year": row["year"], **{col["label"]: row[col["id"]] for col in chart["columns"][1:]}}
                               for row in chart["rows"]]
                    self.assertEqual(adapted, records(frame))
                    for summary in chart["summaries"]:
                        subset = frame[frame["year"].between(summary["from"], summary["to"])]
                        for value in summary["values"]:
                            self.assertAlmostEqual(value["value"], float(subset[value["label"]].sum()), places=9)
        access = DataAccess(self.context)
        self.assertEqual(self.before, tuple(access.data_version(name) for name in LOADERS))

    def test_full_workbook_notes_units_and_region_scope(self):
        service = create_service(self.context, check_interval=0)
        msk, rf = service.report(region="msk"), service.report(region="rf")
        self.assertEqual(len(msk["charts"]), 7)
        self.assertEqual(len(rf["charts"]), 2)
        self.assertTrue(any("нет данных" in note for note in rf["notes"]))
        book = load_workbook(BytesIO(export_workbook(msk)))
        self.assertEqual(len(book.sheetnames), 15)
        for chart in msk["charts"]:
            self.assertEqual(chart["unit"], "млн м²")
            self.assertEqual(book[chart["id"]].max_row, len(chart["rows"]) + 1)


if __name__ == "__main__":
    unittest.main()
