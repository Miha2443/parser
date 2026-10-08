"""Complete XLSX from one immutable economics payload, with literal text cells."""
from io import BytesIO
import json

from openpyxl import Workbook
from openpyxl.styles import Font


def export_workbook(payload):
    book = Workbook()
    book.remove(book.active)

    def sheet(name, headers, rows):
        tab = book.create_sheet(name)
        for row in [headers, *rows]:
            tab.append([json.dumps(value, ensure_ascii=False, allow_nan=False)
                        if isinstance(value, (list, dict)) else value for value in row])
        for row in tab:
            for cell in row:
                if isinstance(cell.value, str):
                    cell.data_type = "s"
        for cell in tab[1]:
            cell.font = Font(bold=True)
        tab.freeze_panes = "A2"
        tab.auto_filter.ref = tab.dimensions
        for column in tab.columns:
            tab.column_dimensions[column[0].column_letter].width = 28

    sheet("Report", ["Field", "Value"], [[key, payload[key]] for key in
          ("schemaVersion", "version", "generatedAt", "family", "selection", "source")])
    sheet("Charts", ["ID", "Title", "Kind", "Unit", "Stack"],
          [[chart.get(key) for key in ("id", "title", "kind", "unit", "stack")] for chart in payload["charts"]])
    columns = []
    for chart in payload["charts"]:
        sheet(chart["id"] + "_series", ["Series ID", "Series", "Unit", "Period", "Value"],
              [[series["id"], series["name"], series["unit"], point["x"], point["y"]]
               for series in chart["series"] for point in series["points"]])
        if "totals" in chart:
            sheet(chart["id"] + "_totals", ["Year", "Total", "Unit"],
                  [[point["x"], point["y"], chart["unit"]] for point in chart["totals"]])
    for item in [*payload["charts"], *payload["tables"]]:
        cols = item["columns"]
        sheet(item["id"], [col["label"] for col in cols],
              [[row.get(col["id"]) for col in cols] for row in item["rows"]])
        unit = next((chart["unit"] for chart in payload["charts"]
                     if item["id"] in (chart["id"], chart["id"] + "_pivot")), None)
        columns += [[item["id"], col["id"], col["label"], unit] for col in cols]
    sheet("Columns", ["Table ID", "Column ID", "Label", "Chart unit"], columns)
    provenance = payload["source"]["provenance"]
    sheet("Provenance", [col["label"] for col in provenance["columns"]],
          [[row.get(col["id"]) for col in provenance["columns"]] for row in provenance["rows"]])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()
