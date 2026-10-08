from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.tdm_routes import create_router
from pipeline.data_access import DataContext


class TdmTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.path = self.root / "data/raw/realty/nashdom/test.json"
        self.path.parent.mkdir(parents=True)
        self.path.write_text('{"value":1}')
        app = FastAPI()
        app.include_router(create_router(DataContext(self.root)))
        self.client = TestClient(app)
        for name, result in (("_get_token", "secret-bot-token"), ("_get_workspace_id", "1"),
                             ("_get_group_id", "2"), ("_is_disabled", False)):
            patcher = patch("backend.tdm_routes.tdm_notify." + name, return_value=result)
            patcher.start()
            self.addCleanup(patcher.stop)
        self.token = self.client.get("/api/v1/tdm/catalog").json()["actionToken"]
        self.headers = {"X-TDM-Token": self.token, "Idempotency-Key": "action-one"}

    def test_no_send_on_load_guard_and_duplicate_prevention(self):
        with patch("backend.tdm_routes.tdm_notify.notify", return_value=True) as notify:
            catalog = self.client.get("/api/v1/tdm/catalog")
            self.assertNotIn("secret-bot-token", catalog.text)
            notify.assert_not_called()
            body = {"kind": "text", "text": "Report"}
            self.assertEqual(self.client.post("/api/v1/tdm/send", json=body).status_code, 403)
            self.assertEqual(self.client.post("/api/v1/tdm/send", json=body,
                             headers={**self.headers, "Origin": "https://untrusted.example"}).status_code, 403)
            for _ in range(2):
                self.assertTrue(self.client.post("/api/v1/tdm/send", json=body, headers=self.headers).json()["ok"])
            notify.assert_called_once_with("Report", group_id=None)

    def test_file_allowlist_upload_cleanup_and_disabled(self):
        with patch("backend.tdm_routes.tdm_notify.notify_file", return_value=True) as notify:
            bad = self.client.post("/api/v1/tdm/send", json={"kind": "file", "key": "../../secret.env"}, headers=self.headers)
            self.assertEqual(bad.status_code, 404)
            captured = []
            def capture(path, **kwargs):
                captured.append(path)
                self.assertEqual(path.read_bytes(), b"uploaded")
                self.assertEqual(path.name, "report.xlsx")
                return True
            notify.side_effect = capture
            response = self.client.post("/api/v1/tdm/upload?filename=../../report.xlsx", content=b"uploaded", headers=self.headers)
            self.assertTrue(response.json()["ok"])
            self.assertFalse(captured[0].exists())
            with patch("backend.tdm_routes.tdm_notify._is_disabled", return_value=True):
                response = self.client.post("/api/v1/tdm/send", json={"kind": "text", "text": "blocked"},
                                            headers={**self.headers, "Idempotency-Key": "blocked"})
                self.assertEqual(response.status_code, 503)

    def test_failed_send_not_repeated_and_credentials_redacted(self):
        with patch("backend.tdm_routes.tdm_notify.notify", side_effect=RuntimeError("secret-bot-token")) as notify:
            for _ in range(2):
                result = self.client.post("/api/v1/tdm/send", json={"kind": "text", "text": "Report"}, headers=self.headers)
                self.assertFalse(result.json()["ok"])
                self.assertNotIn("secret-bot-token", result.text)
            self.assertEqual(notify.call_count, 1)

    def test_upload_size_and_group_validation_never_send(self):
        with patch("backend.tdm_routes.tdm_notify.notify_file") as notify:
            with patch("backend.tdm_routes.MAX_UPLOAD", 4):
                self.assertEqual(self.client.post("/api/v1/tdm/upload", content=b"large", headers=self.headers).status_code, 413)
            self.assertEqual(self.client.post("/api/v1/tdm/upload?group=---1", content=b"test", headers=self.headers).status_code, 400)
            notify.assert_not_called()


if __name__ == "__main__":
    unittest.main()
