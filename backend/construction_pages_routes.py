"""Additional construction-page endpoints; the legacy routes are unchanged."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.construction_pages_export import export_workbook
from backend.domrf_service import DomrfService
from backend.permits_page_service import PermitsPageService


def create_router(context, call, permits_service=None, domrf_service=None):
    permits_service = permits_service or PermitsPageService(context)
    domrf_service = domrf_service or DomrfService(context)
    router = APIRouter(prefix="/api/v1/construction")

    @router.get("/permits")
    def permits(permit_kind: str = Query(default="total", pattern="^(housing|nonresidential|total)$"),
                month: int | None = Query(default=None, ge=1, le=12)):
        return call(lambda: permits_service.report(permit_kind=permit_kind, month=month), scope="Construction permits")

    @router.get("/domrf")
    def domrf(region: str = Query(default="msk", pattern="^(msk|rf)$")):
        return call(lambda: domrf_service.report(region=region), scope="DOM.RF")

    def workbook(payload, version, filename):
        if version != payload["version"]:
            raise HTTPException(409, "Source changed; refresh the displayed report before exporting",
                                headers={"X-Data-Version": payload["version"]})
        content = call(lambda: export_workbook(payload), scope="Construction export")
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"',
                                 "X-Data-Version": payload["version"]})

    @router.get("/permits/export")
    def permits_export(required_version: str = Query(min_length=1, max_length=128),
                       permit_kind: str = Query(default="total", pattern="^(housing|nonresidential|total)$"),
                       month: int | None = Query(default=None, ge=1, le=12)):
        return workbook(permits(permit_kind, month), required_version, "construction-permits")

    @router.get("/domrf/export")
    def domrf_export(required_version: str = Query(min_length=1, max_length=128),
                     region: str = Query(default="msk", pattern="^(msk|rf)$")):
        return workbook(domrf(region), required_version, "construction-domrf")

    return router
