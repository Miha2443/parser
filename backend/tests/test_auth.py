from dataclasses import replace
from contextlib import closing
from io import BytesIO
import json
from pathlib import Path
import sqlite3
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit
from urllib.request import Request as UrlRequest

from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

from backend.auth import (AuthSettings, AuthStore, GRAPH_PATHS, LoginLimited, graph_path,
                          client_address, install_auth)
from starlette.requests import Request


PASSWORD = "test-only-password-12345"


class AuthStoreTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.now = 100000
        self.settings = AuthSettings(enabled=True, database=Path(self.tmp.name) / "auth/db.sqlite3",
                                     secure_cookie=False, session_seconds=1000, idle_seconds=300)
        self.store = AuthStore(self.settings, clock=lambda: self.now)
        self.store.set_user("analyst", PASSWORD, "analyst")
        self.store.set_user("viewer", PASSWORD + "v", "viewer")

    def login(self, username="analyst", address="address", password=PASSWORD):
        return self.store.login(username, password, address)

    def test_hashes_and_hashed_session_ids_only_persist(self):
        token, principal = self.login()
        with closing(sqlite3.connect(self.settings.database)) as db:
            encoded = db.execute("SELECT password_hash FROM users WHERE username='analyst'").fetchone()[0]
            self.assertTrue(encoded.startswith("$argon2id$"))
            data = "\n".join(db.iterdump())
        self.assertNotIn(PASSWORD, data)
        self.assertNotIn(token, data)
        self.assertNotEqual(principal.csrf, token)
        self.assertEqual(self.store.principal(token).public(), {"username": "analyst", "role": "analyst"})

    def test_concurrent_devices_and_logout_are_independent(self):
        first, a = self.login(address="one")
        second, b = self.login(address="two")
        self.assertNotEqual(first, second)
        self.assertNotEqual(a.csrf, b.csrf)
        self.store.logout(a)
        self.assertIsNone(self.store.principal(first))
        self.assertIsNotNone(self.store.principal(second))
        self.assertIsNotNone(AuthStore(self.settings, clock=lambda: self.now).principal(second), "restarts preserve sessions")

    def test_login_rotates_only_the_current_browser(self):
        old, _ = self.login(address="one")
        other, _ = self.login(address="two")
        current, _ = self.store.login("viewer", PASSWORD + "v", "one", old)
        self.assertIsNone(self.store.principal(old))
        self.assertIsNotNone(self.store.principal(other))
        self.assertEqual(self.store.principal(current).role, "viewer")

    def test_password_role_change_disable_and_revoke_invalidate_sessions(self):
        token, _ = self.login()
        self.store.set_user("analyst", PASSWORD, "viewer")
        self.assertIsNone(self.store.principal(token))
        token, _ = self.login()
        self.assertEqual(self.store.principal(token).role, "viewer")
        self.store.revoke_all("analyst")
        self.assertIsNone(self.store.principal(token))
        token, _ = self.login()
        self.store.disable_user("analyst")
        self.assertIsNone(self.store.principal(token))
        self.assertIsNone(self.login())

    def test_idle_and_absolute_expiry_cannot_be_refreshed_after_deadline(self):
        token, _ = self.login()
        self.now += 301
        self.assertIsNone(self.store.principal(token))
        token, _ = self.login(address="two")
        for _ in range(4):
            self.now += 240
            self.assertIsNotNone(self.store.principal(token))
        self.now += 40
        self.assertIsNone(self.store.principal(token))

    def test_limits_are_persistent_and_not_a_global_account_lock(self):
        for _ in range(10):
            self.assertIsNone(self.login(password="incorrect"))
        with self.assertRaises(LoginLimited):
            self.login()
        with self.assertRaises(LoginLimited):
            AuthStore(self.settings, clock=lambda: self.now).login("analyst", PASSWORD, "address")
        self.assertIsNotNone(self.login(address="another-device"))
        self.now += 901
        self.assertIsNotNone(self.login())

    def test_many_successful_shared_account_logins_do_not_exhaust_failure_limits(self):
        for _ in range(15):
            self.assertIsNotNone(self.login())
        with self.store._connect() as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts").fetchone()[0], 0)

    def test_forwarded_address_only_from_explicit_trusted_proxy(self):
        def request(peer, header):
            return Request({"type": "http", "client": (peer, 1234), "headers": [(b"x-real-ip", header.encode())]})
        self.assertEqual(client_address(request("203.0.113.1", "1.2.3.4"), self.settings), "203.0.113.1")
        settings = replace(self.settings, trusted_proxies=("172.18.0.2",))
        self.assertEqual(client_address(request("172.18.0.2", "203.0.113.3"), settings), "203.0.113.3")
        self.assertEqual(client_address(request("172.18.0.2", "forged,header"), settings), "172.18.0.2")
        with patch("backend.auth.socket.getaddrinfo", return_value=[(0, 0, 0, "", ("172.18.0.2", 0))]):
            self.assertEqual(client_address(request("172.18.0.2", "203.0.113.4"), replace(settings, trusted_proxies=("web",))), "203.0.113.4")

    def test_missing_unknown_disabled_and_wrong_passwords_never_issue_sessions(self):
        self.assertIsNone(self.store.login("missing", PASSWORD, "new"))
        self.assertIsNone(self.login(password="incorrect"))
        self.assertIsNone(self.store.principal("unknown-cookie"))
        self.assertIsNone(self.store.principal("x" * 257))
        for username, password, role in (("bad name", PASSWORD, "viewer"), ("user", "short", "viewer"), ("user", PASSWORD, "admin")):
            with self.assertRaises(ValueError):
                self.store.set_user(username, password, role)

    def test_internal_probe_is_signed_short_lived_and_survives_process_restart(self):
        token = self.store.probe_token()
        self.assertTrue(AuthStore(self.settings, clock=lambda: self.now).valid_probe(token))
        self.assertFalse(self.store.valid_probe(token + "x"))
        self.assertFalse(self.store.valid_probe("bad"))
        self.now += 121
        self.assertFalse(self.store.valid_probe(token))

    def test_cli_names_are_normalized_for_disable_and_revoke(self):
        token, _ = self.login()
        self.store.revoke_all(" ANALYST ")
        self.assertIsNone(self.store.principal(token))
        token, _ = self.login()
        self.store.disable_user(" ANALYST ")
        self.assertIsNone(self.store.principal(token))
        with self.assertRaises(ValueError):
            self.store.revoke_all("missing")

    def test_old_attempts_table_migrates_without_resetting_failure_counts_or_accounts(self):
        with self.store._connect() as db:
            db.execute("DROP TABLE attempts")
            db.execute("CREATE TABLE attempts (key TEXT NOT NULL, at REAL NOT NULL)")
            db.execute("INSERT INTO attempts VALUES ('existing-key', ?)", (self.now,))
        upgraded = AuthStore(self.settings, clock=lambda: self.now)
        token, _ = upgraded.login("analyst", PASSWORD, "address")
        self.assertIsNotNone(upgraded.principal(token))
        with upgraded._connect() as db:
            self.assertIn("id", {row[1] for row in db.execute("PRAGMA table_info(attempts)")})
            self.assertEqual(db.execute("SELECT COUNT(*) FROM attempts WHERE key='existing-key'").fetchone()[0], 1)

    def test_an_unknown_role_in_damaged_storage_never_receives_analyst_access(self):
        token, _ = self.login()
        with self.store._connect() as db:
            db.execute("PRAGMA ignore_check_constraints=ON")
            db.execute("UPDATE users SET role='unexpected' WHERE username='analyst'")
        self.assertIsNone(self.store.principal(token))
        self.assertIsNone(self.login())


class AuthApiTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = TemporaryDirectory()
        cls.addClassCleanup(cls.tmp.cleanup)
        cls.settings = AuthSettings(enabled=True, database=Path(cls.tmp.name) / "auth.sqlite3", secure_cookie=False)
        cls.app = FastAPI()
        cls.calls = []

        @cls.app.get("/api/v1/catalog")
        def catalog():
            cls.calls.append("catalog")
            return {"schemaVersion": 1, "profiles": [{"annual": {"rows": [{"year": 2026, "totalM2": 0, "valuesM2": {"residential": 0}}]}}],
                    "source": {"date": "2026-11-10", "files": ["secret.xlsx"], "issues": []}}

        @cls.app.get("/api/v1/profile/export")
        def export():
            cls.calls.append("export")
            return {"download": True}

        @cls.app.get("/api/v1/updates")
        def updates():
            cls.calls.append("updates")
            return {"log": "secret"}

        @cls.app.get("/api/v1/future-private-endpoint")
        def future():
            return {"secret": True}

        @cls.app.get("/api/v1/health")
        def health():
            return {"status": "ok", "release": "/runtime/private", "sources": {"secret": 1}}

        @cls.app.get("/api/v1/sales")
        def broken():
            raise HTTPException(503, "Failed at /private/secret.xlsx")

        @cls.app.post("/api/v1/tdm/send")
        def send():
            cls.calls.append("send")
            return {"ok": True}

        cls.store = install_auth(cls.app, cls.settings)
        cls.store.set_user("analyst", PASSWORD, "analyst")
        cls.store.set_user("viewer", PASSWORD + "v", "viewer")

    def client(self, remote="203.0.113.10", https=False):
        return TestClient(self.app, base_url="https://dashboard.test" if https else "http://dashboard.test", client=(remote, 12345))

    def login(self, client, username="analyst"):
        response = client.post("/api/v1/auth/login", headers={"Origin": "http://dashboard.test"},
                               json={"username": username, "password": PASSWORD + ("v" if username == "viewer" else "")})
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_login_required_before_service_or_export_runs(self):
        client = self.client()
        self.assertEqual(client.get("/api/v1/auth/session").json(), {"enabled": True, "user": None, "csrfToken": None, "secureCookie": False})
        for path in ("/api/v1/catalog", "/api/v1/profile/export", "/api/v1/updates", "/docs", "/docs/oauth2-redirect", "/openapi.json"):
            before = len(self.calls)
            self.assertEqual(client.get(path).status_code, 401, path)
            self.assertEqual(len(self.calls), before)
        self.assertEqual(client.get("/api/v1/catalog", headers={"X-Role": "analyst", "X-User": "analyst"}).status_code, 401)

    def test_roles_restrict_direct_urls_and_unknown_endpoints(self):
        client = self.client(remote="203.0.113.11")
        self.login(client, "viewer")
        for path in ("/api/v1/profile/export", "/api/v1/profile/export/", "/api/v1/updates", "/api/v1/tdm/catalog", "/api/v1/future-private-endpoint", "/openapi.json"):
            self.assertEqual(client.get(path).status_code, 403, path)
        self.assertEqual(client.post("/api/v1/tdm/send").status_code, 403)
        report = client.get("/api/v1/catalog")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertNotIn("secret.xlsx", report.text)
        self.assertEqual(report.json()["profiles"][0]["annual"]["rows"][0]["valuesM2"]["residential"], 0)
        analyst = self.client(remote="203.0.113.12")
        self.login(analyst)
        self.assertEqual(analyst.get("/api/v1/profile/export").status_code, 200)
        self.assertIn("secret.xlsx", analyst.get("/api/v1/catalog").text)

    def test_cookie_attributes_and_csrf_guard_mutations_without_side_effects(self):
        client = self.client(remote="203.0.113.13")
        session = self.login(client)
        cookie = client.cookies[self.settings.cookie_name]
        self.assertNotIn(cookie, json.dumps(session))
        self.assertIn("HttpOnly", client.post("/api/v1/auth/logout", headers={"Origin": "http://dashboard.test", "X-CSRF-Token": session["csrfToken"]}).headers["set-cookie"])
        session = self.login(client)
        before = len(self.calls)
        for headers in ({}, {"Origin": "https://evil.test", "X-CSRF-Token": session["csrfToken"]},
                        {"Origin": "http://dashboard.test", "X-CSRF-Token": "incorrect"}):
            self.assertEqual(client.post("/api/v1/tdm/send", headers=headers).status_code, 403)
        self.assertEqual(len(self.calls), before)
        headers = {"Origin": "http://dashboard.test", "X-CSRF-Token": session["csrfToken"]}
        self.assertEqual(client.post("/api/v1/tdm/send", headers=headers).status_code, 200)
        self.assertEqual(client.post("/api/v1/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(client.get("/api/v1/catalog").status_code, 401)

    def test_login_rejects_cross_origin_missing_origin_bad_json_and_oversized_body(self):
        client = self.client(remote="203.0.113.14")
        body = {"username": "analyst", "password": PASSWORD}
        for origin in (None, "null", "https://evil.test", "http://[invalid"):
            headers = {"Origin": origin} if origin else {}
            self.assertEqual(client.post("/api/v1/auth/login", json=body, headers=headers).status_code, 403)
        headers = {"Origin": "http://dashboard.test", "Content-Type": "application/json"}
        for content in ("[]", "null", "{}", '{"username":1,"password":"x"}', "not json"):
            self.assertEqual(client.post("/api/v1/auth/login", content=content, headers=headers).status_code, 400)
        self.assertEqual(client.post("/api/v1/auth/login", content="x" * 8193, headers=headers).status_code, 413)

    def test_secure_production_cookie_and_explicit_local_compatibility(self):
        app = FastAPI()
        with TemporaryDirectory() as directory:
            settings = replace(self.settings, database=Path(directory) / "secure.sqlite3", secure_cookie=True)
            store = install_auth(app, settings)
            store.set_user("analyst", PASSWORD, "analyst")
            client = TestClient(app, base_url="https://dashboard.test")
            insecure = TestClient(app, base_url="http://dashboard.test")
            self.assertEqual(insecure.post("/api/v1/auth/login", json={"username": "analyst", "password": PASSWORD}, headers={"Origin": "http://dashboard.test"}).status_code, 426)
            response = client.post("/api/v1/auth/login", json={"username": "analyst", "password": PASSWORD}, headers={"Origin": "https://dashboard.test"})
            cookie = response.headers["set-cookie"]
            self.assertTrue(cookie.startswith("__Host-dashboard-session="))
            for attribute in ("Secure", "HttpOnly", "SameSite=lax", "Path=/"):
                self.assertIn(attribute, cookie)
            self.assertNotIn("Domain=", cookie)
        app = FastAPI()
        install_auth(app, replace(self.settings, enabled=False))
        self.assertEqual(TestClient(app).get("/api/v1/auth/session").json(), {"enabled": False, "user": None, "csrfToken": None, "secureCookie": False})

    def test_internal_readiness_cannot_be_used_remotely_or_for_exports_and_tdm(self):
        token = self.store.probe_token()
        headers = {"X-Dashboard-Probe": token}
        remote = self.client()
        local = self.client(remote="127.0.0.1")
        self.assertEqual(remote.get("/api/v1/catalog", headers=headers).status_code, 401)
        self.assertEqual(local.get("/api/v1/catalog", headers=headers).status_code, 200)
        for path in ("/api/v1/profile/export", "/api/v1/tdm/catalog", "/api/v1/updates"):
            self.assertEqual(local.get(path, headers=headers).status_code, 401)
        self.assertEqual(local.post("/api/v1/tdm/send", headers=headers).status_code, 401)
        self.assertEqual(remote.get("/api/v1/health").json(), {"status": "ok"})
        self.assertEqual(local.get("/api/v1/health").json()["release"], "/runtime/private")

    def test_real_readiness_cli_uses_local_signed_probe_without_a_human_account(self):
        from docker.readiness import probe
        local = self.client(remote="127.0.0.1")
        seen = []
        def open_url(request, **kwargs):
            self.assertIsInstance(request, UrlRequest)
            self.assertIn("X-dashboard-probe", dict(request.header_items()))
            response = local.get(urlsplit(request.full_url).path, headers=dict(request.header_items()))
            self.assertEqual(response.status_code, 200, response.text)
            seen.append(request.full_url)
            return BytesIO(response.content)
        def validate(get, **kwargs):
            self.assertIn("secret.xlsx", json.dumps(get("/api/v1/catalog")), "readiness must receive full validation data")
        with patch.dict("os.environ", {"PARSER_AUTH_ENABLED": "1", "PARSER_AUTH_DB": str(self.settings.database)}), \
                patch("docker.readiness.urlopen", side_effect=open_url), patch("docker.readiness.validate_pages", side_effect=validate):
            probe(expected="/runtime/private")
        self.assertEqual(len(seen), 2)

    def test_failed_viewer_reports_do_not_expose_private_diagnostics(self):
        client = self.client(remote="203.0.113.15")
        self.login(client, "viewer")
        response = client.get("/api/v1/sales")
        self.assertEqual(response.status_code, 503)
        self.assertNotIn("secret.xlsx", response.text)

    def test_viewer_session_and_logout_are_auth_payloads_not_graph_projections(self):
        client = self.client(remote="203.0.113.16")
        expected = self.login(client, "viewer")
        response = client.get("/api/v1/auth/session")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), expected)
        response = client.post("/api/v1/auth/logout", headers={"Origin": "http://dashboard.test", "X-CSRF-Token": expected["csrfToken"]})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(response.json()["user"])
        self.assertEqual(client.get("/api/v1/catalog").status_code, 401)

    def test_state_failures_are_closed_and_never_enable_legacy_access(self):
        client = self.client()
        with patch.object(self.store, "principal", side_effect=sqlite3.DatabaseError("private-path")):
            response = client.get("/api/v1/catalog", cookies={self.settings.cookie_name: "token"})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn("private-path", response.text)

    def test_graph_allowlist_is_explicit_and_excludes_exports(self):
        self.assertTrue(all(graph_path(path) for path in GRAPH_PATHS))
        for path in ("/api/v1/future/report", "/api/v1/construction-tree/history/2026-11-01/export", "/api/v1/updates"):
            self.assertFalse(graph_path(path))
        self.assertTrue(graph_path("/api/v1/construction-tree/history/2026-11-01"))


class AuthTdmIntegrationTests(unittest.TestCase):
    def test_remote_analyst_needs_session_csrf_action_token_and_idempotency_key(self):
        from backend.tdm_routes import create_router
        from pipeline.data_access import DataContext
        with TemporaryDirectory() as directory:
            app = FastAPI()
            app.include_router(create_router(DataContext(Path(directory))))
            store = install_auth(app, AuthSettings(enabled=True, database=Path(directory) / "auth.sqlite3", secure_cookie=False))
            store.set_user("analyst", PASSWORD, "analyst")
            store.set_user("viewer", PASSWORD + "v", "viewer")
            analyst = TestClient(app, base_url="http://dashboard.test", client=("203.0.113.30", 1234))
            viewer = TestClient(app, base_url="http://dashboard.test", client=("203.0.113.31", 1234))
            def login(client, username):
                result = client.post("/api/v1/auth/login", json={"username": username, "password": PASSWORD + ("v" if username == "viewer" else "")}, headers={"Origin": "http://dashboard.test"})
                self.assertEqual(result.status_code, 200)
                return result.json()
            self.assertEqual(analyst.get("/api/v1/tdm/catalog").status_code, 401)
            login(viewer, "viewer")
            self.assertEqual(viewer.get("/api/v1/tdm/catalog").status_code, 403)
            session = login(analyst, "analyst")
            with patch("backend.tdm_routes.tdm_notify._get_token", return_value="test-only-bot-token"), \
                    patch("backend.tdm_routes.tdm_notify._get_workspace_id", return_value="1"), \
                    patch("backend.tdm_routes.tdm_notify._get_group_id", return_value="2"), \
                    patch("backend.tdm_routes.tdm_notify._is_disabled", return_value=False), \
                    patch("backend.tdm_routes.tdm_notify.notify", return_value=True) as send:
                catalog = analyst.get("/api/v1/tdm/catalog")
                self.assertEqual(catalog.status_code, 200)
                self.assertNotIn("test-only-bot-token", catalog.text)
                send.assert_not_called()
                headers = {"Origin": "http://dashboard.test", "X-CSRF-Token": session["csrfToken"], "Idempotency-Key": "one-explicit-action"}
                body = {"kind": "text", "text": "Test-only report"}
                self.assertEqual(analyst.post("/api/v1/tdm/send", json=body, headers=headers).status_code, 403)
                send.assert_not_called()
                headers["X-TDM-Token"] = catalog.json()["actionToken"]
                for _ in range(2):
                    self.assertTrue(analyst.post("/api/v1/tdm/send", json=body, headers=headers).json()["ok"])
                send.assert_called_once_with("Test-only report", group_id=None)
                self.assertEqual(viewer.post("/api/v1/tdm/send", json=body, headers=headers).status_code, 403)
                self.assertEqual(send.call_count, 1)

    def test_legacy_local_tdm_cannot_be_exposed_by_reverse_proxy(self):
        from backend.tdm_routes import create_router
        from pipeline.data_access import DataContext
        with TemporaryDirectory() as directory:
            app = FastAPI()
            app.include_router(create_router(DataContext(Path(directory))))
            client = TestClient(app, client=("127.0.0.1", 1234))
            self.assertEqual(client.get("/api/v1/tdm/catalog", headers={"X-Forwarded-For": "203.0.113.30"}).status_code, 403)


if __name__ == "__main__":
    unittest.main()
