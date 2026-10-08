"""Current-construction report and full version-pinned workbook."""
from io import BytesIO
import json

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response
from openpyxl import Workbook

from backend.generation_service import GenerationService
from backend.profile_service import FilterUnavailable
from pipeline.construction_calculations import catalog, report


def build_report(*args, **kwargs):
    try:
        return report(*args, **kwargs)
    except LookupError as exc:
        raise FilterUnavailable(str(exc)) from exc


def create_service(context, check_interval=5):
    return GenerationService(context, ("load_construction_operational", "load_rasprodannost"),
                             ("construction_operational", "rasprodannost"), catalog, build_report, check_interval)


def export_workbook(payload):
    book = Workbook()
    book.remove(book.active)

    def sheet(name, headers, rows):
        tab = book.create_sheet(name)
        tab.append(headers)
        for row in rows:
            values = []
            for value in row:
                if isinstance(value, (dict, list)):
                    value = json.dumps(value, ensure_ascii=False, allow_nan=False)
                values.append("'" + value if isinstance(value, str) and value.lstrip().startswith(("=", "+", "-", "@")) else value)
            tab.append(values)
        tab.freeze_panes = "A2"
        tab.auto_filter.ref = tab.dimensions
        for column in tab.columns:
            tab.column_dimensions[column[0].column_letter].width = 35

    sheet("Report", ["Field", "Value"], [[key, payload[key]] for key in
          ("version", "generatedAt", "selection", "constructionPeriod", "salesReadinessPeriod", "source", "sourceDetails", "notes")])
    sheet("Metrics", ["Indicator", "Value", "Unit"], [[row["label"], row["value"], row["unit"]] for row in payload["metrics"]])
    sheet("Sales", ["Field", "Value"], list((payload["sales"] or {}).items()))
    table = payload["permits"]
    sheet("Permits Moscow", [col["label"] for col in table["columns"]] or ["No data"],
          [[row[col["id"]] for col in table["columns"]] for row in table["rows"]])
    buffer = BytesIO()
    book.save(buffer)
    return buffer.getvalue()


def create_router(context, call, service=None):
    service = service or create_service(context)
    router = APIRouter(prefix="/api/v1/construction")

    @router.get("/catalog")
    def options():
        return call(service.catalog, scope="Construction")

    @router.get("")
    def view(region: str = Query(default="msk", pattern="^(msk|rf)$"),
             permit_kind: str = Query(default="total", pattern="^(total|housing|nonresidential)$"),
             month: int | None = Query(default=None, ge=1, le=12)):
        return call(lambda: service.report(region=region, permit_kind=permit_kind, month=month), scope="Construction")

    @router.get("/export")
    def export(region: str = Query(default="msk", pattern="^(msk|rf)$"),
               permit_kind: str = Query(default="total", pattern="^(total|housing|nonresidential)$"),
               month: int | None = Query(default=None, ge=1, le=12),
               required_version: str = Query(min_length=1, max_length=128)):
        payload = view(region, permit_kind, month)
        if required_version is not None and required_version != payload["version"]:
            raise HTTPException(409, "Source changed; refresh the displayed report before exporting")
        return Response(export_workbook(payload), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="current-construction.xlsx"',
                                 "X-Data-Version": payload["version"]})

    return router
