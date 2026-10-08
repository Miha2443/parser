"""Local read-only API for the React dashboard."""
import logging

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from backend.profile_service import FilterUnavailable, ProfileService, SourceUnavailable
from backend.apartments_service import ApartmentsService
from pipeline.data_access import DataContext

logger = logging.getLogger(__name__)


def create_app(service=None, apartments_service=None):
    app = FastAPI(title="Moscow dashboard", version="1.0.0")
    service = service or ProfileService(DataContext.from_environment())
    app.state.profile_service = service
    apartments_service = apartments_service or ApartmentsService(DataContext.from_environment())
    app.state.apartments_service = apartments_service

    def call(function, *args, scope="Profile"):
        try:
            return function(*args)
        except FilterUnavailable as exc:
            detail = "Developer or region is unavailable" if scope == "Apartments" else "Company or region is unavailable"
            raise HTTPException(404, detail) from exc
        except (SourceUnavailable, OSError, ValueError, RuntimeError, KeyError, TypeError) as exc:
            logger.exception("%s data unavailable", scope)
            raise HTTPException(503, "Source data is unavailable; check server inputs and retry") from exc

    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "dataMode": "live"}

    @app.get("/api/v1/catalog")
    def catalog():
        return call(service.catalog)

    @app.get("/api/v1/profile")
    def profile(developer: str = Query(min_length=1, max_length=200), region: str = Query(pattern="^(msk|rf)$")):
        return call(service.profile, developer, region)

    @app.get("/api/v1/profile/export")
    def export(developer: str = Query(min_length=1, max_length=200), region: str = Query(pattern="^(msk|rf)$"),
               required_version: str | None = Query(default=None, min_length=1, max_length=128)):
        from backend.profile_export import export_workbook
        payload = call(service.profile, developer, region)
        if required_version is not None and required_version != payload["version"]:
            raise HTTPException(409, "Source data changed; refresh the displayed profile before exporting")
        return Response(export_workbook(payload), media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="developer-profile.xlsx"',
                                 "X-Data-Version": payload["version"]})

    @app.get("/api/v1/apartments/catalog")
    def apartments_catalog():
        return call(apartments_service.catalog, scope="Apartments")

    @app.get("/api/v1/apartments")
    def apartments(region: str | None = Query(default=None, pattern="^(msk|rf)$")):
        return call(apartments_service.overview, region, scope="Apartments")

    @app.get("/api/v1/apartments/developer")
    def apartments_developer(developer: str = Query(min_length=1),
                             region: str | None = Query(default=None, pattern="^(msk|rf)$")):
        return call(apartments_service.developer, developer, region, scope="Apartments")

    @app.get("/api/v1/apartments/export")
    def apartments_export(region: str | None = Query(default=None, pattern="^(msk|rf)$"),
                          developer: str | None = Query(default=None, min_length=1),
                          required_version: str | None = Query(default=None, min_length=1, max_length=128)):
        from backend.apartments_export import export_workbook as export_apartments
        payload = (call(apartments_service.overview, region, scope="Apartments") if developer is None else
                   call(apartments_service.developer, developer, region, scope="Apartments"))
        if required_version is not None and required_version != payload["version"]:
            raise HTTPException(409, "Source data changed; refresh the displayed apartments before exporting",
                                headers={"X-Data-Version": payload["version"]})
        content = call(export_apartments, payload, scope="Apartments")
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="apartments.xlsx"',
                                 "X-Data-Version": payload["version"]})

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


app = create_app()
