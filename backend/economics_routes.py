"""Typed economics controls; repeated lists preserve independent block filters."""
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import Response

from backend.economics_export import export_workbook
from backend.economics_service import EconomicsService

Period = Literal["year", "quarter", "month"]
TextList = Annotated[list[str] | None, Query()]
NumberList = Annotated[list[int | Literal[""]] | None, Query()]


def salary_filters(period: Period = "month", region: Annotated[str | None, Query(min_length=1)] = None,
                   views: TextList = None, ytd: bool = False, months: NumberList = None, quarters: NumberList = None):
    return dict(period=period, region=region, views=views, ytd=ytd, months=months, quarters=quarters)


def ipc_filters(period: Period = "month", regions: TextList = None,
                index_base: Literal["month_to_month", "ytd_to_yago"] = "month_to_month",
                months: NumberList = None, quarters: NumberList = None):
    return dict(period=period, regions=regions, index_base=index_base, months=months, quarters=quarters)


def accounts_filters(block1_regions: TextList = None, block2_regions: TextList = None,
                     block3_regions: TextList = None, block4_regions: TextList = None,
                     structure_region: Annotated[str | None, Query(min_length=1)] = None,
                     structure_mode: Literal["value", "share"] = "value", structure_industries: TextList = None,
                     index_region: Annotated[str | None, Query(min_length=1)] = None,
                     index_industries: TextList = None, show_total: bool = True):
    return dict(block1_regions=block1_regions, block2_regions=block2_regions,
                block3_regions=block3_regions, block4_regions=block4_regions,
                structure_region=structure_region, structure_mode=structure_mode, structure_industries=structure_industries,
                index_region=index_region, index_industries=index_industries, show_total=show_total)


def create_router(context, call, services=None):
    router = APIRouter(prefix="/api/v1/economics")
    services = services or {family: EconomicsService(context, family) for family in ("salary", "ipc", "accounts")}

    def register(family, dependency):
        service = services[family]

        def catalog():
            return call(service.catalog, scope="Economics")

        def report(filters: dict = Depends(dependency)):
            return call(lambda: service.report(**filters), scope="Economics")

        def export(required_version: Annotated[str, Query(min_length=1, max_length=128)],
                   filters: dict = Depends(dependency)):
            payload = report(filters)
            if required_version != payload["version"]:
                raise HTTPException(409, "Source changed; refresh the displayed economics report before exporting",
                                    headers={"X-Data-Version": payload["version"]})
            content = call(export_workbook, payload, scope="Economics")
            return Response(content, media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                            headers={"Content-Disposition": f'attachment; filename="economics-{family}.xlsx"',
                                     "X-Data-Version": payload["version"]})

        for suffix, endpoint in (("/catalog", catalog), ("", report), ("/report", report), ("/export", export)):
            router.add_api_route(f"/{family}{suffix}", endpoint, methods=["GET"], name=f"economics_{family}_{endpoint.__name__}")

    for family, dependency in (("salary", salary_filters), ("ipc", ipc_filters), ("accounts", accounts_filters)):
        register(family, dependency)
    return router
