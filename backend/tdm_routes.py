"""Explicit, local-only TDM actions. Never send on page load or automatic retry."""
import secrets
import threading
import re
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

from fastapi import APIRouter, Header, HTTPException, Request
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from app.tdm_files import list_tdm_datasets, list_tdm_realty_files, prepare_tdm_dataset
from pipeline import tdm_notify

MAX_UPLOAD = 50 * 1024 * 1024


class SendAction(BaseModel):
    kind: str
    key: str = ""
    text: str = Field(default="", max_length=10000)
    group: str | None = Field(default=None, max_length=80, pattern=r"^-?\d+$")


def create_router(context):
    router = APIRouter(prefix="/api/v1/tdm")
    token = secrets.token_urlsafe(32)
    completed = {}
    lock = threading.Lock()
    raw_root = (context.root / "data/raw/realty").resolve()

    def status():
        configured = bool(tdm_notify._get_token() and tdm_notify._get_workspace_id() and tdm_notify._get_group_id())
        return {"tokenReady": bool(tdm_notify._get_token()), "workspaceReady": bool(tdm_notify._get_workspace_id()),
                "groupReady": bool(tdm_notify._get_group_id()), "disabled": tdm_notify._is_disabled(),
                "ready": configured and not tdm_notify._is_disabled()}

    def guard(request, supplied):
        # This dashboard is deliberately localhost-only until proper authentication is added.
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "TDM actions require a local connection")
        origin = request.headers.get("origin")
        if origin and urlparse(origin).hostname not in {"localhost", "127.0.0.1", "::1"}:
            raise HTTPException(403, "Untrusted origin")
        if not supplied or not secrets.compare_digest(supplied, token):
            raise HTTPException(403, "Reload the TDM page before sending")

    def send_once(key, action):
        if not key or len(key) > 128:
            raise HTTPException(400, "A unique send key is required")
        if not status()["ready"]:
            raise HTTPException(503, "TDM is disabled or not configured")
        with lock:
            if key in completed:
                if completed[key] is None:
                    raise HTTPException(409, "This send is still in progress")
                return completed[key]
            if len(completed) >= 1000:
                raise HTTPException(429, "Send history is full; restart the local API")
            completed[key] = None
        try:
            ok = bool(action())
            result = {"ok": ok, "message": "Отправлено" if ok else "Отправка не подтверждена. Проверьте чат перед повтором."}
        except Exception:
            # Do not expose credentials, URLs with tokens, or third-party exception bodies.
            result = {"ok": False, "message": "Отправка не подтверждена. Проверьте чат перед повтором."}
        with lock:
            completed[key] = result
        return result

    @router.get("/catalog")
    def catalog(request: Request):
        if request.client and request.client.host not in {"127.0.0.1", "::1", "testclient"}:
            raise HTTPException(403, "TDM requires a local connection")
        datasets = list_tdm_datasets(context.root)
        files = list_tdm_realty_files(raw_root)
        return {"status": status(), "actionToken": token, "maxUploadBytes": MAX_UPLOAD,
                "datasets": [{"key": option.spec.key, "title": option.spec.title, "section": option.spec.section,
                              "files": [path.relative_to(context.root).as_posix() for path in option.files],
                              "size": sum(path.stat().st_size for path in option.files)} for option in datasets],
                "files": [{"key": option.rel, "label": option.label, "size": option.size_bytes} for option in files]}

    @router.post("/groups")
    def groups(request: Request, x_tdm_token: str | None = Header(default=None)):
        guard(request, x_tdm_token)
        if not status()["tokenReady"] or status()["disabled"]:
            raise HTTPException(503, "TDM is disabled or not configured")
        try:
            rows = tdm_notify.get_all_groups()
        except Exception as exc:
            raise HTTPException(502, "Could not retrieve TDM groups") from exc
        return {"groups": [{"groupId": row.get("groupId"),
                            "workspaceId": (row.get("group") or {}).get("workspaceId"),
                            "title": (row.get("group") or {}).get("title") or row.get("groupName") or "",
                            "type": (row.get("group") or {}).get("type")} for row in rows]}

    @router.post("/send")
    def send(body: SendAction, request: Request, x_tdm_token: str | None = Header(default=None),
             idempotency_key: str | None = Header(default=None)):
        guard(request, x_tdm_token)
        if body.kind == "text":
            if not body.text.strip():
                raise HTTPException(400, "Message is empty")
            action = lambda: tdm_notify.notify(body.text.strip(), group_id=body.group)
        elif body.kind == "file":
            options = {option.rel: option for option in list_tdm_realty_files(raw_root)}
            if body.key not in options:
                raise HTTPException(404, "Selected file is unavailable")
            path = options[body.key].path.resolve()
            if not path.is_relative_to(raw_root):
                raise HTTPException(400, "File is outside the source directory")
            action = lambda: tdm_notify.notify_file(path, caption=body.text.strip(), group_id=body.group)
        elif body.kind == "dataset":
            options = {option.spec.key: option for option in list_tdm_datasets(context.root)}
            if body.key not in options:
                raise HTTPException(404, "Selected dataset is unavailable")
            option = options[body.key]
            if any(not path.resolve().is_relative_to(context.root.resolve()) for path in option.files):
                raise HTTPException(400, "Dataset is outside the project")
            def action():
                with TemporaryDirectory(prefix="dashboard-tdm-") as directory:
                    path, _ = prepare_tdm_dataset(option, Path(directory))
                    return tdm_notify.notify_file(path, caption=body.text.strip(), group_id=body.group)
        else:
            raise HTTPException(400, "Unknown send mode")
        return send_once(idempotency_key, action)

    @router.post("/upload")
    async def upload(request: Request, filename: str = "upload.bin", caption: str = "", group: str | None = None,
                     x_tdm_token: str | None = Header(default=None), idempotency_key: str | None = Header(default=None)):
        guard(request, x_tdm_token)
        if len(caption) > 10000 or group and (len(group) > 80 or not re.fullmatch(r"-?\d+", group)):
            raise HTTPException(400, "Invalid caption or group")
        name = Path(filename.replace("\\", "/")).name
        if not name or name in {".", ".."} or len(name) > 200:
            raise HTTPException(400, "Invalid filename")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_UPLOAD:
                raise HTTPException(413, "File exceeds 50 MiB")
        if not content:
            raise HTTPException(400, "File is empty")
        def action():
            with TemporaryDirectory(prefix="dashboard-tdm-") as directory:
                path = Path(directory) / name
                path.write_bytes(content)
                return tdm_notify.notify_file(path, caption=caption.strip(), group_id=group)
        return await run_in_threadpool(send_once, idempotency_key, action)

    return router
