"""Deployment safety contracts that do not require a running Docker engine."""
from contextlib import redirect_stdout, redirect_stderr
from io import StringIO
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from backend.auth import AuthSettings, AuthStore
from backend.auth_cli import main

try:
    import yaml
except ImportError:
    yaml = None

ROOT = Path(__file__).resolve().parents[2]


class AuthDeploymentTests(unittest.TestCase):
    @unittest.skipUnless(yaml, "Install backend/requirements-dev.txt for Compose YAML checks")
    def test_compose_keeps_updater_profile_and_auth_outside_immutable_release(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        backend = compose["services"]["backend"]
        self.assertEqual(compose["services"]["updater"]["profiles"], ["updater"])
        self.assertEqual(backend["environment"]["PARSER_AUTH_ENABLED"], "${PARSER_AUTH_ENABLED:-1}")
        self.assertEqual(backend["environment"]["PARSER_AUTH_DB"], "/auth/accounts.sqlite3")
        self.assertIn("auth_state:/auth", backend["volumes"])
        self.assertIn("./runtime:/runtime:ro", backend["volumes"])
        self.assertIsNone(compose["volumes"]["auth_state"])
        self.assertNotIn("ports", backend, "private backend must not publish a bypass port")

    @unittest.skipUnless(yaml, "Install backend/requirements-dev.txt for Compose YAML checks")
    def test_optional_ssh_binding_preserves_the_existing_web_port_default(self):
        compose = yaml.safe_load((ROOT / "docker-compose.yml").read_text(encoding="utf-8"))
        self.assertEqual(compose["services"]["web"]["ports"], ["${WEB_LISTEN:-${WEB_PORT:-8080}}:8080"])
        self.assertEqual(compose["services"]["backend"]["environment"]["PARSER_AUTH_ALLOW_HTTP"], "${PARSER_AUTH_ALLOW_HTTP:-0}")

    def test_nginx_preserves_origin_authority_and_does_not_accept_internal_probe_header(self):
        for name in ("docker/nginx.conf", "deploy/linux/nginx-parser.conf"):
            source = (ROOT / name).read_text(encoding="utf-8")
            self.assertIn("proxy_set_header Host $http_host;", source)
            self.assertIn('proxy_set_header X-Dashboard-Probe "";', source)
            self.assertIn("proxy_set_header X-Real-IP $remote_addr;", source)
            self.assertIn("location = /profile-snapshot.json { return 404; }", source)
            self.assertIn("location = /api/v1/health { return 404; }", source)
        self.assertIn("rm -f dist/profile-snapshot.json", (ROOT / "docker/frontend.Dockerfile").read_text(encoding="utf-8"))

    def test_cli_bootstrap_is_atomic_and_passwords_do_not_appear_in_output(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "accounts.sqlite3"
            store = AuthStore(AuthSettings(database=path))
            output, errors = StringIO(), StringIO()
            with patch("backend.auth_cli.password", side_effect=["test-password-analyst-123", "short"]), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(["--db", str(path), "bootstrap"]), 1)
            self.assertEqual(store.list_users(), [], "invalid second password must not leave a half-configured installation")
            with patch("backend.auth_cli.password", side_effect=["test-password-analyst-123", "test-password-viewer-456"]), redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(["--db", str(path), "bootstrap"]), 0)
            self.assertEqual({row["role"] for row in store.list_users()}, {"analyst", "viewer"})
            self.assertNotIn("test-password-analyst-123", output.getvalue() + errors.getvalue())
            self.assertNotIn("test-password-viewer-456", output.getvalue() + errors.getvalue())
            with patch("backend.auth_cli.password") as prompt, redirect_stdout(output), redirect_stderr(errors):
                self.assertEqual(main(["--db", str(path), "bootstrap"]), 1)
                prompt.assert_not_called()

    def test_settings_secure_defaults_and_explicit_http_local_mode(self):
        with patch.dict("os.environ", {}, clear=True):
            settings = AuthSettings.from_environment()
            self.assertFalse(settings.enabled)
            self.assertTrue(settings.secure_cookie)
        with patch.dict("os.environ", {"PARSER_AUTH_ENABLED": "1", "PARSER_AUTH_ALLOW_HTTP": "1"}):
            settings = AuthSettings.from_environment()
            self.assertTrue(settings.enabled)
            self.assertFalse(settings.secure_cookie)
        with patch.dict("os.environ", {"PARSER_AUTH_ENABLED": "yes"}):
            with self.assertRaises(ValueError):
                AuthSettings.from_environment()


if __name__ == "__main__":
    unittest.main()
