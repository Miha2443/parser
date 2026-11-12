"""Additional construction-page endpoints; the legacy routes are unchanged."""
from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import Response

from backend.construction_pages_export import export_workbook
from backend.domrf_service import DomrfService
from backend.permits_page_service import PermitSelectionInvalid, PermitsPageService
from backend.permit_history_details import PermitHistoryDetails


def create_router(context, call, permits_service=None, domrf_service=None, permit_history_details=None):
    permits_service = permits_service or PermitsPageService(context)
    domrf_service = domrf_service or DomrfService(context)
    permit_history_details = permit_history_details if permit_history_details is not None else PermitHistoryDetails()
    router = APIRouter(prefix="/api/v1/construction")

    @router.get("/permits")
    def permits(permit_kind: str = Query(default="total", pattern="^(housing|nonresidential|total)$"),
                month: int | None = Query(default=None, ge=1, le=12),
                structure_mode: str = Query(default="period", pattern="^(period|annual)$"),
                structure_month_from: int = Query(default=1, ge=1, le=12),
                structure_month_to: int | None = Query(default=None, ge=1, le=12),
                structure_year_from: int | None = Query(default=None),
                structure_year_to: int | None = Query(default=None)):
        def selected():
            try:
                return permits_service.report(permit_kind=permit_kind, month=month, structure_mode=structure_mode,
                                              structure_month_from=structure_month_from, structure_month_to=structure_month_to,
                                              structure_year_from=structure_year_from, structure_year_to=structure_year_to)
            except PermitSelectionInvalid as exc:
                raise HTTPException(422, str(exc)) from exc
        return call(selected, scope="Construction permits")

    @router.get("/domrf")
    def domrf(region: str = Query(default="msk", pattern="^(msk|rf)$")):
        return call(lambda: domrf_service.report(region=region), scope="DOM.RF")

    def require_version(payload, version):
        if version != payload["version"]:
            raise HTTPException(409, "Source changed; refresh the displayed report before exporting",
                                headers={"X-Data-Version": payload["version"]})

    def workbook(payload, version, filename, permit_details=None):
        require_version(payload, version)
        content = call(lambda: export_workbook(payload, permit_details) if permit_details is not None
                       else export_workbook(payload), scope="Construction export")
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": f'attachment; filename="{filename}.xlsx"',
                                 "X-Data-Version": payload["version"]})

    @router.get("/permits/export")
    def permits_export(required_version: str = Query(min_length=1, max_length=128),
                       permit_kind: str = Query(default="total", pattern="^(housing|nonresidential|total)$"),
                       month: int | None = Query(default=None, ge=1, le=12),
                       structure_mode: str = Query(default="period", pattern="^(period|annual)$"),
                       structure_month_from: int = Query(default=1, ge=1, le=12),
                       structure_month_to: int | None = Query(default=None, ge=1, le=12),
                       structure_year_from: int | None = Query(default=None),
                       structure_year_to: int | None = Query(default=None)):
        payload = permits(permit_kind, month, structure_mode, structure_month_from, structure_month_to,
                          structure_year_from, structure_year_to)
        require_version(payload, required_version)
        selection = payload["selection"]
        years = [row["year"] for row in payload["permits"]["presentation"]["rows"]]
        structure = selection["structure"]

        def details():
            main = permit_history_details.select(
                permit_kind, month_from=1, month_to=selection["month"],
                year_from=min(years, default=2022), year_to=max(years, default=2022))
            # The main chart and structure have independent period controls.
            chosen = [index for index, row in enumerate(main["rows"]) if row[17].year in years]
            main["rows"] = [main["rows"][index] for index in chosen]
            if "source_rows" in main:
                main["source_rows"] = [main["source_rows"][index] for index in chosen]
            main["notes"] = [*main["notes"],
                             "Object sums may differ from the agreed graph totals; graph values are unchanged."]
            breakdown = permit_history_details.select(
                permit_kind, month_from=1 if structure["mode"] == "annual" else structure["monthFrom"],
                month_to=12 if structure["mode"] == "annual" else structure["monthTo"],
                year_from=structure["yearFrom"], year_to=structure["yearTo"])
            if main["source"] != breakdown["source"]:
                raise RuntimeError("Historical permit details changed during export")
            breakdown["notes"] = [*breakdown["notes"],
                                  "This sheet follows the structure controls, not the main chart period."]
            return {"main": main, "structure": breakdown}

        selected_details = call(details, scope="Construction permit object export")
        return workbook(payload, required_version, "construction-permits", selected_details)

    @router.get("/domrf/export")
    def domrf_export(required_version: str = Query(min_length=1, max_length=128),
                     region: str = Query(default="msk", pattern="^(msk|rf)$")):
        return workbook(domrf(region), required_version, "construction-domrf")

    return router
