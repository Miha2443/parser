"""Local dashboard API: read-only analytics and explicit TDM actions."""
import logging
from pickle import UnpicklingError
from zipfile import BadZipFile

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import Response

from backend.profile_service import FilterUnavailable, ProfileService, SourceUnavailable
from backend.apartments_service import ApartmentsService
from backend.sales_service import SalesService
from pipeline.data_access import DataContext

logger = logging.getLogger(__name__)


def create_app(service=None, apartments_service=None, sales_service=None):
    app = FastAPI(title="Moscow dashboard", version="1.0.0")
    service = service or ProfileService(DataContext.from_environment())
    app.state.profile_service = service
    apartments_service = apartments_service or ApartmentsService(DataContext.from_environment())
    app.state.apartments_service = apartments_service
    sales_service = sales_service or SalesService(DataContext.from_environment())
    app.state.sales_service = sales_service

    def call(function, *args, scope="Profile"):
        try:
            return function(*args)
        except FilterUnavailable as exc:
            detail = {"Sales": "Sales region or period is unavailable",
                      "Apartments": "Developer or region is unavailable",
                      "Profile": "Company or region is unavailable"}.get(scope, f"{scope} filter is unavailable")
            raise HTTPException(404, detail) from exc
        except (SourceUnavailable, OSError, ValueError, RuntimeError, KeyError, TypeError,
                BadZipFile, UnpicklingError, EOFError) as exc:
            logger.exception("%s data unavailable", scope)
            raise HTTPException(503, "Source data is unavailable; check server inputs and retry") from exc

    from backend.linear_routes import create_router as create_linear_router
    app.include_router(create_linear_router(DataContext.from_environment(), call))
    from backend.operational_routes import create_router as create_operational_router
    app.include_router(create_operational_router(DataContext.from_environment(), call))
    from backend.construction_routes import create_router as create_construction_router
    app.include_router(create_construction_router(DataContext.from_environment(), call))
    from backend.annual_routes import create_router as create_annual_router
    app.include_router(create_annual_router(DataContext.from_environment(), call))
    from backend.economics_routes import create_router as create_economics_router
    app.include_router(create_economics_router(DataContext.from_environment(), call))
    from backend.map_routes import create_router as create_map_router
    app.include_router(create_map_router(DataContext.from_environment(), call))
    from backend.updates_routes import create_router as create_updates_router
    app.include_router(create_updates_router(DataContext.from_environment(), call))
    from backend.tdm_routes import create_router as create_tdm_router
    app.include_router(create_tdm_router(DataContext.from_environment()))

    @app.get("/api/v1/health")
    def health():
        # A process that cannot load its pinned release is not ready for traffic.
        payload = call(service.catalog)
        if not payload.get("controls", {}).get("developers"):
            raise HTTPException(503, "No developer data available")
        return {"status": "ok", "dataMode": "live",
                "release": str(service.context.root) if hasattr(service, "context") else None}

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

    @app.get("/api/v1/sales/catalog")
    def sales_catalog():
        return call(sales_service.catalog, scope="Sales")

    @app.get("/api/v1/sales")
    def sales(region: str | None = Query(default=None, min_length=1, max_length=200),
              period: str | None = Query(default=None, min_length=1, max_length=200)):
        return call(sales_service.detail, region, period, scope="Sales")

    @app.get("/api/v1/sales/export")
    def sales_export(required_version: str = Query(min_length=1, max_length=128),
                     region: str | None = Query(default=None, min_length=1, max_length=200),
                     period: str | None = Query(default=None, min_length=1, max_length=200)):
        from backend.sales_export import export_workbook as export_sales
        payload = call(sales_service.detail, region, period, scope="Sales")
        if required_version != payload["version"]:
            raise HTTPException(409, "Source data changed; refresh the displayed sales before exporting",
                                headers={"X-Data-Version": payload["version"]})
        content = call(export_sales, payload, scope="Sales")
        return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                        headers={"Content-Disposition": 'attachment; filename="sales-readiness.xlsx"',
                                 "X-Data-Version": payload["version"]})

    @app.middleware("http")
    async def no_store(request, call_next):
        response = await call_next(request)
        response.headers["Cache-Control"] = "no-store"
        return response

    return app


app = create_app()
