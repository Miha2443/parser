"""Read-only map endpoints and complete filtered workbook."""
from io import BytesIO

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from openpyxl import Workbook

from backend.map_service import MapService


def workbook(payload):
    book = Workbook()
    meta = book.active
    meta.title = "Report"
    meta.append(["Version", payload["version"]])
    meta.append(["Source date", payload["source"]["date"]])
    for note in payload["notes"]:
        meta.append(["Note", note])
    for name, value in payload["selection"].items():
        meta.append([name, str(value)])
    sheet = book.create_sheet("Objects")
    headers = list(payload["rows"][0]) if payload["rows"] else ["id", "address", "developer", "quality"]
    sheet.append(headers)
    for row in payload["rows"]:
        sheet.append(["'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@"))
                      else value for value in (row.get(key) for key in headers)])
    sheet.freeze_panes = "A2"
    sheet.auto_filter.ref = sheet.dimensions
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def create_router(context, call, service=None):
    router = APIRouter(prefix="/api/v1/map")
    service = service or MapService(context)

    @router.get("/catalog")
    def catalog():
        return call(service.catalog, scope="Map")

    @router.get("")
    def report(developer: str | None = None, status: list[str] = Query(default=[]),
               okrug: list[str] = Query(default=[]), year_from: int | None = None,
               year_to: int | None = None, quality: str = "all", only_with_coords: bool = True):
        return call(service.report, developer, status, okrug, year_from, year_to, quality,
                    only_with_coords, scope="Map")

    @router.get("/export")
    def export(required_version: str = Query(min_length=1, max_length=128),
               developer: str | None = None, status: list[str] = Query(default=[]),
               okrug: list[str] = Query(default=[]), year_from: int | None = None,
               year_to: int | None = None, quality: str = "all", only_with_coords: bool = True):
        payload = call(service.report, developer, status, okrug, year_from, year_to, quality,
                       only_with_coords, scope="Map")
        if required_version != payload["version"]:
            raise HTTPException(409, "Map inputs changed; refresh before exporting")
        return Response(workbook(payload), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="map-objects.xlsx"',
                                 "X-Data-Version": payload["version"]})

    return router
