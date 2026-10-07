import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

import pandas as pd
import requests

import fedstat_checker as fedstat


def _write_chunk(path: Path, year: int) -> None:
    frame = pd.DataFrame([
        ["Регион", "Категория", year],
        ["Москва", "Всего", 1.0],
    ])
    path.parent.mkdir(parents=True, exist_ok=True)
    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        frame.to_excel(writer, sheet_name="Данные", header=False, index=False)


class FedstatResilienceTests(unittest.TestCase):
    def test_run_checkpoint_survives_process_retry(self):
        with tempfile.TemporaryDirectory() as tmp:
            checkpoint_path = Path(tmp) / "checkpoint.json"
            with patch.object(fedstat, "RUN_CHECKPOINT_FILE", checkpoint_path):
                checkpoint = fedstat._load_run_checkpoint("run-1")
                fedstat._mark_run_completed(checkpoint, "43246", "07.07.2025")
                loaded = fedstat._load_run_checkpoint("run-1")
                self.assertIn("43246", loaded["completed"])
                self.assertEqual(
                    loaded["completed"]["43246"]["remote_date"], "07.07.2025"
                )
                self.assertEqual(
                    fedstat._load_run_checkpoint("run-2")["completed"], {}
                )

    def test_34118_reuses_valid_chunks_after_interruption(self):
        payload = {
            "id": "34118",
            "title": "test",
            "selectedFilterIds": [
                "0_34118", "3_2023", "30611_950292",
                "33560_1540222", "33560_1540224",
                "57831_1", "58389_10", "58389_11",
            ],
        }
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            resume_dir = root / "resume"
            save_path = root / "result.xls"
            calls = []
            failed_second = {"count": 0}

            def interrupted(_indicator_id, _save_dir, **kwargs):
                path = kwargs["save_path_override"]
                calls.append(path.name)
                if path.name.endswith("002.xls"):
                    failed_second["count"] += 1
                    return None
                _write_chunk(path, 2023)
                return path

            with patch.object(fedstat, "FEDSTAT_34118_RESUME_DIR", resume_dir), \
                    patch.object(fedstat, "FEDSTAT_34118_CHUNK_RETRY_SLEEP", 0), \
                    patch.object(fedstat, "download_excel", side_effect=interrupted):
                first = fedstat._download_34118_period_chunks(
                    "34118_часть2", payload, root, save_path,
                    remote_date="19.09.2026", period_chunk_size=1,
                )
            self.assertIsNone(first)
            self.assertEqual(calls.count("34118_34118_часть2_chunk001.xls"), 1)
            self.assertEqual(failed_second["count"], fedstat.FEDSTAT_34118_CHUNK_RETRIES)

            resumed_calls = []

            def resumed(_indicator_id, _save_dir, **kwargs):
                path = kwargs["save_path_override"]
                resumed_calls.append(path.name)
                _write_chunk(path, 2023)
                return path

            with patch.object(fedstat, "FEDSTAT_34118_RESUME_DIR", resume_dir), \
                    patch.object(fedstat, "download_excel", side_effect=resumed):
                result = fedstat._download_34118_period_chunks(
                    "34118_часть2", payload, root, save_path,
                    remote_date="19.09.2026", period_chunk_size=1,
                )
            self.assertIsNotNone(result)
            self.assertEqual(resumed_calls, ["34118_34118_часть2_chunk002.xls"])

    def test_fast_chunk_failure_does_not_enter_slow_fallbacks(self):
        response = Mock()
        response.raise_for_status.side_effect = requests.HTTPError("503")
        session = Mock()
        session.post.return_value = response
        payload = {
            "id": "34118",
            "title": "test",
            "selectedFilterIds": ["0_34118", "3_2023", "58389_10"],
        }
        with tempfile.TemporaryDirectory() as tmp, \
                patch.object(fedstat.requests, "Session", return_value=session), \
                patch.object(fedstat, "_export_post_with_token", side_effect=lambda s, d, i, p, refresh=False: p), \
                patch.object(fedstat, "_download_34118_sdmx_as_excel") as sdmx, \
                patch.object(fedstat, "_download_excel_via_browser") as browser:
            result = fedstat.download_excel(
                "34118_часть2", Path(tmp), remote_date="19.09.2026",
                payload_template_override=payload,
                save_path_override=Path(tmp) / "chunk.xls",
                allow_34118_chunks=False, fast_fail=True,
            )
        self.assertIsNone(result)
        self.assertEqual(session.post.call_count, 1)
        self.assertIn("files", session.post.call_args.kwargs)
        sdmx.assert_not_called()
        browser.assert_not_called()


if __name__ == "__main__":
    unittest.main()
