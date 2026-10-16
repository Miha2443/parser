"""Unfiltered access to the ready monitoring construction tree."""
from fastapi import APIRouter

from backend.construction_tree_service import ConstructionTreeService


def create_router(context, call, service=None):
    router = APIRouter(prefix="/api/v1/construction")
    service = service or ConstructionTreeService(context)

    @router.get("/tree")
    def tree():
        return call(service.report, scope="Construction tree")

    return router
