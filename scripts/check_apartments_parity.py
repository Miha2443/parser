"""Compare API data with execution of both original apartment pages.

Imports/UI are replaced by a capture stub. No original data is modified;
this validates calculation/render data, not source truth or Streamlit layout.
"""
from __future__ import annotations

import argparse
import ast
import json
import sys
from html import escape
from pathlib import Path
from urllib.parse import urlencode
from urllib.request import urlopen

import pandas as pd
import plotly.express as px
import plotly.graph_objects as go

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from pipeline.data_access import DataAccess, DataContext
from pipeline.profile_calculations import clean

ROOMS = ["1 комн", "2 комн", "3 комн", "4+ комн"]
SHARES = ["доля_1комн_%_num", "доля_2комн_%_num", "доля_3комн_%_num", "доля_4+комн_%_num"]


class Capture:
    def __init__(self, region, developer=None):
        self.region, self.developer = region, developer
        self.frames, self.exports, self.figures, self.metrics = [], [], [], []
        self.column_config = self

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def columns(self, widths):
        return [self] * (widths if isinstance(widths, int) else len(widths))

    def expander(self, *args, **kwargs):
        return self

    def radio(self, label, options, **kwargs):
        assert self.region in options
        return self.region

    def selectbox(self, label, options, **kwargs):
        assert self.developer in options
        return self.developer

    def dataframe(self, frame, **kwargs):
        self.frames.append(frame.copy())

    def metric(self, label, value, *args, **kwargs):
        self.metrics.append((label, value))

    def plotly_chart(self, figure, **kwargs):
        self.figures.append(figure)

    def stop(self):
        raise RuntimeError("Original page stopped: missing required source data")

    def __getattr__(self, name):
        return lambda *args, **kwargs: None


def capture(root, filename, data, region, developer=None):
    path = root / "app/pages" / filename
    tree = ast.parse(path.read_text(encoding="utf-8"))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    ui = Capture(region, developer)
    env = {"st": ui, "pd": pd, "px": px, "go": go, "escape": escape,
           "COLORS": {k: "#333333" for k in ("green", "blue", "amber", "red", "muted")},
           "apply_theme": lambda: None, "page_header": lambda *args, **kwargs: None,
           "style_plotly": lambda *args, **kwargs: None, "chart_data_expander": lambda *args, **kwargs: None,
           "table_download_buttons": lambda frame, **kwargs: ui.exports.append(frame.copy()),
           "load_kvartirografia": lambda: data}
    exec(compile(tree, str(path), "exec"), env)
    return ui, env


def row_values(row):
    return [row["name"], row["apartmentThousandCount"], row["areaThousandM2"],
            *[next((room["sharePercent"] for room in row["rooms"] if room["type"] == name), None) for name in ROOMS]]


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:8000")
    parser.add_argument("--root", type=Path, default=ROOT)
    args = parser.parse_args()
    access = DataAccess(DataContext(args.root))
    before = access.data_version("load_kvartirografia")
    data = access.load_kvartirografia()

    def get(path, **query):
        with urlopen(f"{args.url.rstrip('/')}/api/v1/{path}?{urlencode(query)}", timeout=120) as response:
            return json.load(response)

    catalog = get("apartments/catalog")
    cases = []
    for region in [row["id"] for row in catalog["regions"]]:
        api = get("apartments", region=region)
        ui, env = capture(args.root, "4_Квартирография.py", data, region)
        expected_apartments = ui.frames[0].values.tolist()
        actual_apartments = [[r["type"], env["ru_num"](r["count"]), env["ru_num"](r["areaThousandM2"])] for r in api["apartments"]]
        assert actual_apartments == expected_apartments, (region, "apartment table")
        distribution = [[r["range"], r["sharePercent"]] for r in api["distribution"]]
        expected_distribution = list(map(list, zip(ui.figures[0].data[0].x, ui.figures[0].data[0].y)))
        assert clean(distribution) == clean(expected_distribution), (region, "distribution chart")
        for name, frame in zip(("developers", "regions"), ui.exports):
            assert clean([row_values(r) for r in api[name][:50]]) == clean(frame.values.tolist()), (region, name, "legacy top50")
        assert api["reportDate"] == data["report_date"], (region, "report date")
        cases.append(f"overview:{region}")

        choices = catalog["developersByRegion"][region]
        indices = sorted({0, min(1, len(choices) - 1), min(14, len(choices) - 1), len(choices) - 1})
        for index in indices:
            name = choices[index]["id"]
            api = get("apartments/developer", region=region, developer=name)
            ui, env = capture(args.root, "5_Квартирография_по_девелоперу.py", data, region, name)
            summary = api["summary"]
            metric_map = dict(ui.metrics)
            assert metric_map["Квартиры"] == f"{env['ru_num'](summary['countThousand'], 1)} тыс. шт", (name, "count")
            assert metric_map["Площадь"] == f"{env['ru_num'](summary['areaThousandM2'])} тыс. м²", (name, "area")
            if "Ср. площадь квартиры" in metric_map:
                assert metric_map["Ср. площадь квартиры"] == f"{env['ru_num'](summary['averageAreaM2'], 1)} м²", (name, "average")
            assert metric_map["Доля рынка региона"] == f"{env['ru_num'](summary['marketSharePercent'], 2)}%", (name, "market share")
            assert clean([[r["type"], r["sharePercent"]] for r in api["rooms"]]) == clean(ui.frames[0].values.tolist()), (name, "room shares")
            actual_comparison = [[r["name"], r["place"], r["areaThousandM2"], *row_values(r)[3:]] for r in api["comparison"]]
            assert clean(actual_comparison) == clean(ui.frames[-1].values.tolist()), (name, "comparison top10+selected")
            assert summary["place"] == int(env["row"]["place"])
            assert summary["totalDevelopers"] == len(env["devs"])
            cases.append(f"developer:{region}:{name}")
    assert before == access.data_version("load_kvartirografia"), "Source files changed during validation"
    print(json.dumps({"result": "passed", "cases": cases, "scope": "Captured original data/calculations; not source-truth certification"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
