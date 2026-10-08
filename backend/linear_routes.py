"""Linear-object endpoints and complete version-pinned Excel export."""
from io import BytesIO

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from openpyxl import Workbook
from openpyxl.styles import Font, PatternFill

from backend.linear_service import LinearService


def export_workbook(payload):
    book = Workbook()
    meta = book.active
    meta.title = "Report"
    meta.append(["Field", "Value"])
    for key, value in {**payload["selection"], "version": payload["version"],
                       "sourceDate": payload["source"]["date"], "sourceFile": payload["source"]["files"][0]}.items():
        meta.append([key, value])
    headers = ["code", "indicator", "unit", "year", "quarter", "plan", "fact", "percent"]
    for name, rows in [("Selected period", payload["summary"]), ("All quarters", payload["allPeriods"])]:
        tab = book.create_sheet(name)
        tab.append(headers)
        for row in rows:
            tab.append(["'" + row[key] if isinstance(row[key], str) and row[key].lstrip().startswith(("=", "+", "-", "@"))
                        else row[key] for key in headers])
    for tab in book:
        tab.freeze_panes = "A2"
        tab.auto_filter.ref = tab.dimensions
        for cell in tab[1]:
            cell.font = Font(bold=True, color="FFFFFF")
            cell.fill = PatternFill("solid", fgColor="1C3444")
        for column in tab.columns:
            tab.column_dimensions[column[0].column_letter].width = 22 if column[0].value != "indicator" else 65
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def create_router(context, call, service=None):
    router = APIRouter(prefix="/api/v1/linear")
    service = service or LinearService(context)

    @router.get("/catalog")
    def catalog():
        return call(service.catalog, scope="Linear")

    @router.get("")
    def report(year: int | None = None, quarter: int | None = Query(default=None, ge=1, le=4),
               cumulative: bool = False, indicator: str | None = None):
        return call(service.report, year, quarter, cumulative, indicator, scope="Linear")

    @router.get("/export")
    def export(year: int | None = None, quarter: int | None = Query(default=None, ge=1, le=4),
               cumulative: bool = False, indicator: str | None = None,
               required_version: str = Query(min_length=1, max_length=128)):
        payload = call(service.report, year, quarter, cumulative, indicator, scope="Linear")
        if required_version is not None and required_version != payload["version"]:
            raise HTTPException(409, "Source changed; refresh the displayed report before exporting")
        return Response(export_workbook(payload), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="linear-objects.xlsx"',
                                 "X-Data-Version": payload["version"]})

    return router
