"""Complete version-pinned construction workbooks, without display rounding."""
from io import BytesIO

from openpyxl import Workbook


def _safe(value):
    return "'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value


def _leaves(value, path=""):
    if isinstance(value, dict) and value:
        for key, item in value.items():
            escaped = str(key).replace("~", "~0").replace("/", "~1")
            yield from _leaves(item, path + "/" + escaped)
    elif isinstance(value, list) and value:
        for index, item in enumerate(value):
            yield from _leaves(item, path + "/" + str(index))
    else:
        kind = "null" if value is None else "object" if isinstance(value, dict) else "array" if isinstance(value, list) else type(value).__name__
        yield [path, kind, None if isinstance(value, (dict, list)) else value]


def export_workbook(payload):
    book = Workbook()
    book.remove(book.active)

    def sheet(name, headers, rows):
        tab = book.create_sheet(name)
        for row in [headers, *rows]:
            # Notes and source strings are untrusted even when they are labels.
            tab.append([_safe(value) for value in row])
        tab.freeze_panes = "A2"
        tab.auto_filter.ref = tab.dimensions
        for column in tab.columns:
            tab.column_dimensions[column[0].column_letter].width = 30
        return tab

    sheet("Report", ["Field", "Value"], [[field, payload[field]] for field in ("schemaVersion", "version", "generatedAt")])
    source = payload["source"]
    sheet("Sources", ["Field", "Value"], [["date", source["date"]]] + [["file", name] for name in source["files"]] +
          [[issue["level"], issue["message"]] for issue in source["issues"]])
    sheet("Notes", ["Note"], [[note] for note in payload["notes"]])
    if "permits" in payload:
        selection = payload["selection"]
        sheet("Selection", ["Field", "Value"], list(selection.items()))
        table = payload["permits"]
        sheet("Permits", [col["label"] for col in table["columns"]] or ["No data"],
              [[row.get(col["id"]) for col in table["columns"]] for row in table["rows"]])
        chart = table["chart"]
        sheet("Permit Chart", ["Year", "Period", "Remainder", "Total", "Growth pct"],
              [[year] + [chart[field][index]["value"] for field in ("period", "remainder", "totals", "growth")]
               for index, year in enumerate(chart["x"])] if chart else [])
        forecast = payload["forecast"]
        fields = ["year", "value", "housing_living", "housing_total", "mop_embedded", "nonresidential",
                  "offices", "industry", "social", "trade", "hotels", "other"]
        sheet("Forecast", fields, [[row[field] for field in fields] for row in forecast["rows"]])
        sheet("Forecast Sources", ["Year", "Field", "File", "Sheet", "Cell"],
              [[row["year"], field, forecast["source"]["file"], forecast["source"]["sheet"], address]
               for row in forecast["rows"] for field, address in row["cells"].items()])
        sheet("Forecast Notes", ["Field", "Value"], [["unit", forecast["unit"]], ["unitBasis", forecast["unitBasis"]]] +
              [["note", note] for note in forecast["notes"]])
        for index, block in enumerate(payload["breakdowns"], 1):
            sheet(f"Breakdown {index}", [column["label"] for column in block["columns"]],
                  [[row.get(column["id"]) for column in block["columns"]] for row in block["rows"]])
            sheet(f"Breakdown {index} Notes", ["Field", "Value"],
                  [[key, block[key]] for key in ("id", "title", "unit", "periodLabel")] + [["note", note] for note in block["notes"]])
    else:
        sheet("Region", ["Field", "Value"], list(payload["region"].items()))
        for block in ("construction", "sales"):
            item = payload[block]
            sheet(block.title(), ["Field", "Value"],
                  [[key, value] for key, value in (item["period"] or {}).items()] + list((item["metrics"] or {}).items()))
            fields = ["living", "total"] if block == "construction" else sorted({key for row in item["history"] for key in row["metrics"]})
            sheet(block.title() + " History", ["Period", "Label", "Report date", "Source file", *fields],
                  [[row["period"], row["label"], row["reportDate"], row["sourceFile"]] +
                   [(row if block == "construction" else row["metrics"]).get(field) for field in fields]
                   for row in item["history"]])
    # Scalar leaves avoid Excel's single-cell JSON truncation and include every
    # control, source note, null, chart point and forecast-cell address verbatim.
    leaves = []
    for path, kind, value in _leaves(payload):
        if isinstance(value, str) and len(value) > 30000:
            for offset in range(0, len(value), 30000):
                leaves.append([path, kind, offset // 30000, value[offset:offset + 30000]])
        else:
            leaves.append([path, kind, 0, value])
    sheet("Full Payload", ["JSON pointer", "Type", "Part", "Value"], leaves)
    buffer = BytesIO()
    book.save(buffer)
    book.close()
    return buffer.getvalue()
