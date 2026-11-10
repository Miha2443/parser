"""Loopback-only auth integration fixture; never imports the production app."""
from __future__ import annotations

import argparse
from copy import deepcopy
import hmac
import os
from pathlib import Path
import tempfile
from urllib.parse import urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import Response
import uvicorn

from backend.auth import AuthSettings, graph_path, install_auth, loopback


FIXTURE_PASSWORD = "auth-browser-fixture-only-2026"


def create_app(database: Path, setup_token: str) -> FastAPI:
    if not setup_token or len(setup_token) < 24:
        raise ValueError("A temporary fixture setup token is required")
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)
    store = install_auth(app, AuthSettings(enabled=True, database=database, secure_cookie=False))
    store.set_user("viewer", FIXTURE_PASSWORD, "viewer")
    store.set_user("analyst", FIXTURE_PASSWORD, "analyst")
    payloads: dict[str, dict] = {}
    state = {"registrations": 0, "reports": 0, "exports": 0, "tdmActions": 0,
             "requests": [], "missingFixtures": []}

    def private_setup(request: Request):
        if not request.client or not loopback(request.client.host):
            raise HTTPException(403, "Loopback fixture control only")
        supplied = request.headers.get("x-fixture-token", "")
        if not supplied.isascii() or not hmac.compare_digest(supplied, setup_token):
            raise HTTPException(403, "Fixture control token required")

    @app.middleware("http")
    async def observe(request: Request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/api/"):
            principal = getattr(request.state, "principal", None)
            # Never record bodies, cookies, passwords or CSRF tokens.
            state["requests"].append({"method": request.method, "path": request.url.path,
                "status": response.status_code, "role": principal.role if principal else None,
                "host": request.headers.get("host"), "origin": request.headers.get("origin")})
        return response

    @app.get("/health")
    @app.get("/api/v1/health")
    def health():
        return {"status": "ok", "fixture": True}

    @app.post("/__fixture")
    async def register(request: Request):
        private_setup(request)
        body = await request.json()
        key, payload = body.get("key"), body.get("payload")
        if not isinstance(key, str) or not isinstance(payload, dict):
            raise HTTPException(400, "Expected a report key and JSON object")
        parsed = urlsplit(key)
        if parsed.scheme or parsed.netloc or parsed.fragment or not graph_path(parsed.path):
            raise HTTPException(400, "Only graph fixture paths can be registered")
        payloads[key] = deepcopy(payload)
        state["registrations"] += 1
        return {"registered": True}

    @app.get("/__fixture/state")
    def inspect(request: Request):
        private_setup(request)
        return deepcopy(state)

    @app.get("/api/v1/{path:path}")
    def report(path: str, request: Request):
        if path.rstrip("/").endswith("/export"):
            state["exports"] += 1
            return Response(b"auth-browser-fixture-export-only\n", media_type="application/octet-stream",
                headers={"Content-Disposition": 'attachment; filename="fixture-report.xlsx"',
                         "X-Data-Version": request.query_params.get("required_version", "fixture")})
        if path == "tdm/catalog":
            return {"actionToken": "fixture-no-provider", "maxUploadBytes": 1024,
                "status": {"tokenReady": True, "workspaceReady": True, "groupReady": True,
                           "disabled": False, "ready": True}, "datasets": [], "files": []}
        if path == "updates":
            return {"fixture": True, "updates": []}
        key = request.url.path + ("?" + request.url.query if request.url.query else "")
        payload = payloads.get(key, payloads.get(request.url.path))
        if payload is None:
            state["missingFixtures"].append(key)
            raise HTTPException(404, "No registered browser report fixture")
        state["reports"] += 1
        return deepcopy(payload)

    @app.post("/api/v1/tdm/{path:path}")
    def dummy_tdm(path: str):
        # AuthMiddleware must authorize role, Origin and CSRF before this is reached.
        state["tdmActions"] += 1
        return {"ok": True, "fixture": True, "providerCalled": False}

    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, required=True)
    parser.add_argument("--port", type=int, required=True)
    args = parser.parse_args()
    database = args.db.resolve()
    if not database.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        parser.error("The fixture database must be inside the system temporary directory")
    if not 1 <= args.port <= 65535:
        parser.error("Invalid loopback port")
    token = os.environ.get("AUTH_BROWSER_FIXTURE_TOKEN", "")
    if len(token) < 24:
        parser.error("AUTH_BROWSER_FIXTURE_TOKEN must contain a temporary setup token")
    uvicorn.run(create_app(database, token), host="127.0.0.1", port=args.port,
                log_level="warning", access_log=False)


if __name__ == "__main__":
    main()
