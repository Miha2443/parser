"""ETL status is observational: these endpoints never launch collectors."""
from fastapi import APIRouter

from backend.updates_service import UpdatesService


def create_router(context, call, service=None):
    router = APIRouter(prefix="/api/v1/updates")
    service = service or UpdatesService(context)

    @router.get("")
    def report(days: int = 30, monitoring_days: int = 30, only_errors: bool = False):
        return call(service.report, days, monitoring_days, only_errors, scope="Updates")

    return router
