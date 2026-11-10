"""Server-owned accounts, revocable sessions and explicit graph-only access."""
from __future__ import annotations

from dataclasses import dataclass
from contextlib import closing, contextmanager
import hashlib
import hmac
import ipaddress
import json
import logging
import os
from pathlib import Path
import re
import secrets
import socket
import sqlite3
import threading
import time
from urllib.parse import urlsplit

from argon2 import PasswordHasher
from argon2.exceptions import VerificationError, InvalidHashError
from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from starlette.concurrency import run_in_threadpool
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger(__name__)
ROLES = frozenset({"analyst", "viewer"})
USERNAME = re.compile(r"[a-z][a-z0-9_.-]{2,63}\Z")
PASSWORDS = PasswordHasher(time_cost=2, memory_cost=19456, parallelism=1)
HASH_SLOTS = threading.BoundedSemaphore(4)
AUTH_PATHS = frozenset({"/api/v1/auth/session", "/api/v1/auth/login", "/api/v1/auth/logout"})
GRAPH_PATHS = frozenset({
    "/api/v1/catalog", "/api/v1/profile", "/api/v1/apartments/catalog", "/api/v1/apartments",
    "/api/v1/apartments/developer", "/api/v1/sales/catalog", "/api/v1/sales",
    "/api/v1/construction/catalog", "/api/v1/construction", "/api/v1/construction/permits",
    "/api/v1/construction/domrf", "/api/v1/construction/tree", "/api/v1/construction-tree/history",
    "/api/v1/linear/catalog", "/api/v1/linear", "/api/v1/linear/dashboard",
    "/api/v1/map/catalog", "/api/v1/map",
    *[f"/api/v1/commissioning/{family}{suffix}" for family in ("annual", "operational") for suffix in ("", "/catalog")],
    *[f"/api/v1/economics/{family}{suffix}" for family in ("salary", "ipc", "accounts") for suffix in ("", "/catalog", "/report")],
    *[f"/api/v1/{family}{suffix}" for family in ("mortgage", "preferential-mortgage") for suffix in ("", "/catalog")],
})


def graph_path(path):
    return path in GRAPH_PATHS or bool(re.fullmatch(r"/api/v1/construction-tree/history/\d{4}-\d{2}-\d{2}", path))


def loopback(host):
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


@dataclass(frozen=True)
class AuthSettings:
    enabled: bool = False
    database: Path = Path(__file__).resolve().parents[1] / "state/auth/accounts.sqlite3"
    secure_cookie: bool = True
    session_seconds: int = 12 * 60 * 60
    idle_seconds: int = 4 * 60 * 60
    trusted_proxies: tuple[str, ...] = ()

    @property
    def cookie_name(self):
        return "__Host-dashboard-session" if self.secure_cookie else "dashboard_session"

    @classmethod
    def from_environment(cls):
        enabled = os.environ.get("PARSER_AUTH_ENABLED", "0")
        if enabled not in {"0", "1"}:
            raise ValueError("PARSER_AUTH_ENABLED must be 0 or 1")
        allow_http = os.environ.get("PARSER_AUTH_ALLOW_HTTP", "0")
        if allow_http not in {"0", "1"}:
            raise ValueError("PARSER_AUTH_ALLOW_HTTP must be 0 or 1")
        return cls(enabled=enabled == "1", database=Path(os.environ.get("PARSER_AUTH_DB", str(cls.database))),
                   secure_cookie=allow_http != "1", trusted_proxies=tuple(filter(None, os.environ.get("PARSER_AUTH_TRUSTED_PROXIES", "").split(","))))


@dataclass(frozen=True)
class Principal:
    username: str
    role: str
    csrf: str
    session_hash: str

    def public(self):
        return {"username": self.username, "role": self.role}


def digest(value):
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def equal_secret(left, right):
    return isinstance(left, str) and left.isascii() and hmac.compare_digest(left, right)


class LoginLimited(Exception):
    pass


class AuthStore:
    def __init__(self, settings, clock=time.time):
        self.settings, self.clock = settings, clock
        self._ready = False
        self._lock = threading.Lock()
        self._dummy = None

    @contextmanager
    def _connect(self):
        if not self._ready:
            with self._lock:
                if not self._ready:
                    path = self.settings.database
                    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
                    if path.is_symlink():
                        raise OSError("Authentication database must not be a symbolic link")
                    handle = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
                    os.close(handle)
                    if os.name != "nt":
                        os.chmod(path, 0o600)
                    with closing(sqlite3.connect(path, timeout=5)) as db, db:
                        db.executescript("""
                            PRAGMA journal_mode=WAL;
                            CREATE TABLE IF NOT EXISTS users (
                                username TEXT PRIMARY KEY, password_hash TEXT NOT NULL,
                                role TEXT NOT NULL CHECK(role IN ('analyst','viewer')),
                                enabled INTEGER NOT NULL DEFAULT 1);
                            CREATE TABLE IF NOT EXISTS sessions (
                                token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                                csrf TEXT NOT NULL, created REAL NOT NULL,
                                expires REAL NOT NULL, seen REAL NOT NULL);
                            CREATE INDEX IF NOT EXISTS sessions_user ON sessions(username);
                            CREATE TABLE IF NOT EXISTS attempts (id TEXT NOT NULL, key TEXT NOT NULL, at REAL NOT NULL);
                            CREATE INDEX IF NOT EXISTS attempts_key ON attempts(key, at);
                            CREATE TABLE IF NOT EXISTS auth_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                            CREATE TABLE IF NOT EXISTS audit (at REAL NOT NULL, event TEXT NOT NULL, username TEXT NOT NULL);
                        """)
                        if "id" not in {row[1] for row in db.execute("PRAGMA table_info(attempts)")}:
                            db.execute("ALTER TABLE attempts ADD COLUMN id TEXT")
                        db.execute("INSERT OR IGNORE INTO auth_meta VALUES ('probe_key', ?)", (secrets.token_hex(32),))
                    self._ready = True
        db = sqlite3.connect(self.settings.database, timeout=5)
        db.row_factory = sqlite3.Row
        try:
            with db:
                yield db
        finally:
            db.close()

    @staticmethod
    def _encoded_user(username, password, role):
        username = username.strip().lower()
        if not USERNAME.fullmatch(username) or role not in ROLES:
            raise ValueError("Use a 3-64 character Latin username and analyst/viewer role")
        if not isinstance(password, str) or len(password) < 14 or len(password.encode("utf-8")) > 1024:
            raise ValueError("Password must contain at least 14 characters and at most 1024 UTF-8 bytes")
        with HASH_SLOTS:
            password_hash = PASSWORDS.hash(password)
        return username, password_hash, role

    def set_user(self, username, password, role):
        username, password_hash, role = self._encoded_user(username, password, role)
        with self._connect() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?, 1) ON CONFLICT(username) DO UPDATE SET password_hash=excluded.password_hash, role=excluded.role, enabled=1",
                       (username, password_hash, role))
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            db.execute("INSERT INTO audit VALUES (?, 'account_changed', ?)", (self.clock(), username))

    def bootstrap(self, credentials):
        if set(credentials) != ROLES or credentials["analyst"] == credentials["viewer"]:
            raise ValueError("Provide different initial analyst and viewer passwords")
        prepared = [self._encoded_user(username, credentials[username], username) for username in ("analyst", "viewer")]
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM users LIMIT 1").fetchone():
                raise ValueError("Accounts already exist. Use set-user.")
            db.executemany("INSERT INTO users VALUES (?, ?, ?, 1)", prepared)
            db.executemany("INSERT INTO audit VALUES (?, 'account_created', ?)", [(self.clock(), row[0]) for row in prepared])

    def list_users(self):
        with self._connect() as db:
            return [dict(row) for row in db.execute("SELECT username, role, enabled FROM users ORDER BY username")]

    def disable_user(self, username):
        username = username.strip().lower()
        with self._connect() as db:
            if not db.execute("UPDATE users SET enabled=0 WHERE username=?", (username,)).rowcount:
                raise ValueError("Account not found")
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            db.execute("INSERT INTO audit VALUES (?, 'account_disabled', ?)", (self.clock(), username))

    def revoke_all(self, username):
        username = username.strip().lower()
        with self._connect() as db:
            if not db.execute("SELECT 1 FROM users WHERE username=?", (username,)).fetchone():
                raise ValueError("Account not found")
            db.execute("DELETE FROM sessions WHERE username=?", (username,))
            db.execute("INSERT INTO audit VALUES (?, 'sessions_revoked', ?)", (self.clock(), username))

    def _reserve_attempt(self, username, address):
        now, attempt = self.clock(), secrets.token_hex(16)
        keys = (("ip:" + digest(address), 60), ("pair:" + digest(address + "\0" + username), 10))
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            db.execute("DELETE FROM attempts WHERE at < ?", (now - 900,))
            db.execute("DELETE FROM audit WHERE at < ?", (now - 90 * 86400,))
            for key, maximum in keys:
                if db.execute("SELECT COUNT(*) FROM attempts WHERE key=? AND at>=?", (key, now - 900)).fetchone()[0] >= maximum:
                    raise LoginLimited()
            db.executemany("INSERT INTO attempts (id, key, at) VALUES (?, ?, ?)", [(attempt, key, now) for key, _ in keys])
        return attempt

    def login(self, username, password, address, old_token=None):
        username = username.strip().lower()
        attempt = self._reserve_attempt(username, address)
        with self._connect() as db:
            row = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
        with HASH_SLOTS:
            if self._dummy is None:
                self._dummy = PASSWORDS.hash(secrets.token_urlsafe(32))
            encoded = row["password_hash"] if row else self._dummy
            try:
                correct = PASSWORDS.verify(encoded, password)
            except (VerificationError, InvalidHashError):
                correct = False
            replacement = PASSWORDS.hash(password) if correct and row and PASSWORDS.check_needs_rehash(encoded) else None
        if not correct or not row or not row["enabled"] or row["role"] not in ROLES:
            logger.info("Dashboard login rejected")
            return None
        token, csrf, now = secrets.token_urlsafe(32), secrets.token_urlsafe(32), self.clock()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            current = db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            if not current or not current["enabled"] or current["password_hash"] != encoded or current["role"] not in ROLES:
                return None
            if replacement:
                db.execute("UPDATE users SET password_hash=? WHERE username=?", (replacement, username))
            db.execute("DELETE FROM sessions WHERE expires<=? OR seen<=?", (now, now - self.settings.idle_seconds))
            if old_token:
                db.execute("DELETE FROM sessions WHERE token_hash=?", (digest(old_token),))
            db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
                       (digest(token), username, csrf, now, now + self.settings.session_seconds, now))
            db.execute("DELETE FROM attempts WHERE id=?", (attempt,))
            db.execute("INSERT INTO audit VALUES (?, 'login', ?)", (now, username))
        return token, Principal(username, current["role"], csrf, digest(token))

    def principal(self, token):
        if not token or len(token) > 256:
            return None
        now = self.clock()
        with self._connect() as db:
            row = db.execute("SELECT s.*, u.role FROM sessions s JOIN users u USING(username) WHERE s.token_hash=? AND u.enabled=1 AND s.expires>? AND s.seen>?",
                             (digest(token), now, now - self.settings.idle_seconds)).fetchone()
            if not row or row["role"] not in ROLES:
                return None
            if row["seen"] < now - 60:
                db.execute("UPDATE sessions SET seen=? WHERE token_hash=?", (now, row["token_hash"]))
            return Principal(row["username"], row["role"], row["csrf"], row["token_hash"])

    def logout(self, principal):
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (principal.session_hash,))
            db.execute("INSERT INTO audit VALUES (?, 'logout', ?)", (self.clock(), principal.username))

    def probe_token(self):
        # Issued only by the local readiness CLI, never by an HTTP endpoint.
        with self._connect() as db:
            key = db.execute("SELECT value FROM auth_meta WHERE key='probe_key'").fetchone()[0]
        body = f"{int(self.clock())}.{secrets.token_hex(16)}"
        return body + "." + hmac.new(key.encode(), body.encode(), hashlib.sha256).hexdigest()

    def valid_probe(self, token):
        if not token or len(token) > 200:
            return False
        try:
            at, nonce, signature = token.split(".")
            if not re.fullmatch(r"[0-9a-f]{32}", nonce) or not 0 <= self.clock() - int(at) <= 120:
                return False
            with self._connect() as db:
                key = db.execute("SELECT value FROM auth_meta WHERE key='probe_key'").fetchone()[0]
            return equal_secret(signature, hmac.new(key.encode(), f"{at}.{nonce}".encode(), hashlib.sha256).hexdigest())
        except (ValueError, TypeError):
            return False


def same_origin(request):
    origin = request.headers.get("origin")
    if not origin or origin == "null":
        return False
    try:
        parsed = urlsplit(origin)
        return parsed.scheme in {"http", "https"} and not parsed.username and not parsed.password and not parsed.path and not parsed.query and not parsed.fragment and parsed.netloc.casefold() == request.headers.get("host", "").casefold()
    except ValueError:
        return False


def client_address(request, settings):
    peer = request.client.host if request.client else "unknown"
    supplied = request.headers.get("x-real-ip")
    if not supplied:
        return peer
    trusted = False
    for configured in settings.trusted_proxies:
        configured = configured.strip()
        try:
            trusted = ipaddress.ip_address(peer) in ipaddress.ip_network(configured, strict=False)
        except ValueError:
            try:
                trusted = any(row[4][0] == peer for row in socket.getaddrinfo(configured, None))
            except OSError:
                continue
        if trusted:
            break
    if trusted:
        try:
            return str(ipaddress.ip_address(supplied))
        except ValueError:
            pass
    return peer


def session_payload(store, principal=None):
    return {"enabled": store.settings.enabled, "user": principal.public() if principal else None,
            "csrfToken": principal.csrf if principal else None, "secureCookie": store.settings.secure_cookie}


def auth_router(store):
    router = APIRouter(prefix="/api/v1/auth")

    @router.get("/session")
    def session(request: Request):
        return session_payload(store, getattr(request.state, "principal", None))

    @router.post("/login")
    async def login(request: Request):
        if not store.settings.enabled:
            raise HTTPException(409, "Authentication is disabled in this local environment")
        if not same_origin(request):
            raise HTTPException(403, "Untrusted request origin")
        if store.settings.secure_cookie and urlsplit(request.headers["origin"]).scheme != "https":
            raise HTTPException(426, "HTTPS is required for login")
        if request.headers.get("content-type", "").split(";")[0].strip() != "application/json":
            raise HTTPException(415, "JSON is required")
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > 8192:
                raise HTTPException(413, "Login request is too large")
        try:
            body = json.loads(content)
            username, password = body["username"], body["password"]
            if not isinstance(username, str) or not isinstance(password, str) or len(username) > 64 or len(password.encode("utf-8")) > 1024:
                raise ValueError()
        except (ValueError, KeyError, TypeError):
            raise HTTPException(400, "Invalid login request") from None
        try:
            address = await run_in_threadpool(client_address, request, store.settings)
            result = await run_in_threadpool(store.login, username, password, address, request.cookies.get(store.settings.cookie_name))
        except LoginLimited:
            raise HTTPException(429, "Too many login attempts. Try again in 15 minutes.", headers={"Retry-After": "900"}) from None
        if not result:
            raise HTTPException(401, "Invalid username or password")
        token, principal = result
        response = JSONResponse(session_payload(store, principal))
        response.set_cookie(store.settings.cookie_name, token, max_age=store.settings.session_seconds,
                            secure=store.settings.secure_cookie, httponly=True, samesite="lax", path="/")
        return response

    @router.post("/logout")
    async def logout(request: Request):
        principal = getattr(request.state, "principal", None)
        if principal:
            await run_in_threadpool(store.logout, principal)
        response = JSONResponse(session_payload(store))
        response.delete_cookie(store.settings.cookie_name, path="/", secure=store.settings.secure_cookie, httponly=True, samesite="lax")
        return response

    return router


class AuthMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, store):
        super().__init__(app)
        self.store = store

    async def dispatch(self, request, call_next):
        path = request.url.path.rstrip("/") or "/"
        protected = path.startswith(("/api/", "/docs/")) or path in {"/docs", "/redoc", "/openapi.json"}
        if not protected:
            return await call_next(request)
        try:
            return await self._dispatch(request, call_next, path)
        except (sqlite3.Error, OSError):
            logger.exception("Authentication state unavailable")
            return self.error(503, "Authentication service is unavailable")

    @staticmethod
    def error(status, detail):
        return JSONResponse({"detail": detail}, status_code=status, headers={"Cache-Control": "no-store"})

    async def _dispatch(self, request, call_next, path):
        store, principal, internal = self.store, None, False
        health = path == "/api/v1/health" and request.method == "GET"
        if store.settings.enabled:
            local = bool(request.client and loopback(request.client.host))
            if request.method == "GET" and local and (graph_path(path) or path in {"/api/v1/availability", "/api/v1/health"}):
                internal = await run_in_threadpool(store.valid_probe, request.headers.get("x-dashboard-probe"))
            if not internal:
                principal = await run_in_threadpool(store.principal, request.cookies.get(store.settings.cookie_name))
            if path not in AUTH_PATHS and not health and not internal:
                if principal is None:
                    return self.error(401, "Authentication required")
                if principal.role == "viewer" and (request.method not in {"GET", "HEAD"} or not graph_path(path)):
                    return self.error(403, "Analyst access required")
            if path == "/api/v1/auth/logout" and request.method == "POST" and principal is None:
                return self.error(401, "Authentication required")
            if principal and request.method not in {"GET", "HEAD", "OPTIONS"}:
                if not same_origin(request) or not equal_secret(request.headers.get("x-csrf-token", ""), principal.csrf):
                    return self.error(403, "Request verification failed")
        request.state.principal = principal
        request.state.auth_enabled = store.settings.enabled
        response = await call_next(request)
        if store.settings.enabled and principal and principal.role == "viewer" and response.status_code >= 400:
            # Source diagnostics may contain file paths, even on a failed report.
            response = JSONResponse({"detail": {"code": "source_unavailable" if response.status_code == 503 else "request_failed",
                "reason": "Данные временно недоступны." if response.status_code == 503 else "Не удалось выполнить запрос."}}, status_code=response.status_code)
        if store.settings.enabled and response.status_code < 300 and response.headers.get("content-type", "").startswith("application/json"):
            viewer = principal and principal.role == "viewer" and graph_path(path) and not internal
            viewer_health = principal and principal.role == "viewer" and health
            public_health = health and (viewer_health or not (request.client and loopback(request.client.host)))
            if viewer or public_health:
                raw = b"".join([part async for part in response.body_iterator])
                payload = json.loads(raw)
                if public_health:
                    payload = {"status": payload.get("status", "ok")}
                else:
                    from backend.viewer_payload import viewer_payload
                    payload = await run_in_threadpool(viewer_payload, path, payload)
                headers = {key: value for key, value in response.headers.items() if key not in {"content-length", "content-type"}}
                response = JSONResponse(payload, status_code=response.status_code, headers=headers, background=response.background)
        response.headers["Cache-Control"] = "no-store"
        response.headers["Vary"] = "Cookie"
        response.headers["X-Content-Type-Options"] = "nosniff"
        return response


def install_auth(app, settings=None):
    store = AuthStore(settings or AuthSettings.from_environment())
    app.state.auth = store
    app.include_router(auth_router(store))
    app.add_middleware(AuthMiddleware, store=store)
    return store
