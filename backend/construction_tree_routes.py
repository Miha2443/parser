"""Unfiltered access to the ready monitoring construction tree."""
import csv
from io import StringIO
from datetime import date

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response

from backend.construction_tree_service import ConstructionTreeService


def create_router(context, call, service=None):
    router = APIRouter()
    service = service or ConstructionTreeService(context)

    @router.get("/api/v1/construction/tree")
    def tree():
        return call(service.report, scope="Construction tree")

    def history():
        from pipeline.construction_tree_history import read_history
        return read_history(context.root)

    # A separate namespace keeps immutable history independent of live-source
    # availability middleware, including when today's monitoring is absent.
    @router.get("/api/v1/construction-tree/history")
    def versions():
        payload = call(history, scope="Construction tree history")
        return {"schemaVersion": 1, "snapshots": [
            {**row["snapshot"], "version": row["version"], "source": row["source"]}
            for _, row in sorted(payload["snapshots"].items(), reverse=True)]}

    def selected(as_of):
        try:
            if date.fromisoformat(as_of).isoformat() != as_of:
                raise ValueError()
        except ValueError as exc:
            raise HTTPException(422, "Use YYYY-MM-DD") from exc
        payload = call(history, scope="Construction tree history")["snapshots"].get(as_of)
        if payload is None:
            raise HTTPException(404, "No published snapshot for this date")
        return payload

    @router.get("/api/v1/construction-tree/history/{as_of}")
    def snapshot(as_of: str):
        return selected(as_of)

    @router.get("/api/v1/construction-tree/history/{as_of}/export")
    def snapshot_csv(as_of: str):
        return tree_csv(selected(as_of), as_of)

    @router.get("/api/v1/construction-tree/export")
    def current_csv():
        return tree_csv(call(service.report, scope="Construction tree"), "current")

    return router


def tree_csv(payload, selection):
    output = StringIO(newline="")
    writer = csv.writer(output, delimiter=";", lineterminator="\r\n")
    writer.writerow(["ID", "Label", "million_m2", "Evidence", "File", "Sheet", "Version", "Snapshot date", "Published at", "Downloaded at"])
    source = payload["source"]
    snapshot = payload.get("snapshot", {})
    rows = [(m["id"], m["label"], m["value"], m["cell"], source["sheet"]) for m in payload["metrics"]]
    sectors = payload.get("nonresidentialSectors", {})
    rows += [(m["id"], m["label"], m["value"], ",".join(str(r) for r in m["rows"]), sectors["sheet"]) for m in sectors.get("items", [])]
    for identifier, label, value, evidence, sheet in rows:
        row = [identifier, label, "" if value is None else value, evidence, source["file"], sheet, payload["version"], snapshot.get("date", ""), snapshot.get("publishedAt", ""), source["downloadedAt"]]
        writer.writerow(["'" + cell if isinstance(cell, str) and cell.lstrip().startswith(("=", "+", "-", "@")) else cell for cell in row])
    return Response("\ufeff" + output.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="construction-tree-{selection}-{payload["version"][:16]}.csv"'})
