"""Offline checks of dashboard collection scope; all writes use temporary files."""
from __future__ import annotations

from contextlib import ExitStack, redirect_stdout
from io import StringIO
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import fedstat_checker as fc
from pipeline.download_provenance import receipt_path, record_successful_download
from pipeline.registry import INDICATORS as PIPELINE_INDICATORS


class FedstatScopeChecks(unittest.TestCase):
    def setUp(self):
        stack = self.enterContext(ExitStack())
        self.root = Path(stack.enter_context(tempfile.TemporaryDirectory()))
        self.downloads = self.root / "downloads"
        self.state_path = self.root / "state.json"
        self.initial = {src: "01.07.2026" for src in fc.INDICATORS}
        self.initial["legacy-source"] = "01.01.2020"
        self.state_path.write_text(json.dumps(self.initial), encoding="utf-8")
        stack.enter_context(patch.multiple(fc, DOWNLOAD_DIR=self.downloads,
                                          STATE_FILE=self.state_path))
        stack.enter_context(patch.dict(os.environ, {"FEDSTAT_ONLY_IDS": ""}))
        self.driver = Mock()
        self.create = stack.enter_context(patch.object(fc, "create_driver", return_value=self.driver))
        self.date = stack.enter_context(patch.object(fc, "get_last_update_date", return_value="02.07.2026"))
        self.calls = []
        self.failed = set()

        @record_successful_download
        def download(src, save_dir, **kwargs):
            self.calls.append(src)
            if src in self.failed:
                return None
            path = save_dir / f"{src}.xls"
            path.write_bytes(b"offline fixture")
            return path

        stack.enter_context(patch.object(fc, "download_excel", side_effect=download))
        self.output = stack.enter_context(redirect_stdout(StringIO()))

    def assert_sources(self, expected):
        self.assertEqual([call.args[1] for call in self.date.call_args_list], expected)
        self.assertEqual(self.calls, expected)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        self.assertEqual(state, {src: "02.07.2026" if src in expected else date
                                 for src, date in self.initial.items()})
        self.assertEqual({p.stem for p in self.downloads.glob("*.xls")}, set(expected))
        for src in expected:
            receipt = json.loads(receipt_path(self.downloads / f"{src}.xls").read_text(encoding="utf-8"))
            self.assertEqual(receipt["source_id"], src)
            self.assertEqual(receipt["remote_date"], "02.07.2026")
        self.driver.quit.assert_called_once()

    def test_default_matches_dashboard_consumers_and_keeps_heavy_exports_last(self):
        registry_ids = {src for item in PIPELINE_INDICATORS if item.source == "fedstat"
                        for src in item.source_ids}
        direct_loader_ids = {"34118_часть1", "34118_часть2"}
        self.assertEqual(set(fc.DEFAULT_INDICATOR_USAGE), registry_ids | direct_loader_ids)
        self.assertTrue(set(fc.DEFAULT_INDICATOR_USAGE) <= set(fc.INDICATORS))
        expected = ["43246", "57824", "31074_часть1", "31074_часть2",
                    "34118_часть1", "34118_часть2"]
        self.assertEqual(fc.main([]), 0)
        self.assert_sources(expected)
        self.assertIn("активны по умолчанию (6)", self.output.getvalue())
        self.assertIn("Отключены по умолчанию (24)", self.output.getvalue())

    def test_force_downloads_only_default_even_when_all_dates_unchanged(self):
        self.date.return_value = "01.07.2026"
        self.assertEqual(fc.main(["--force"]), 0)
        self.assertEqual(self.calls, list(fc.DEFAULT_INDICATOR_USAGE))
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8")), self.initial)

    def test_unchanged_default_is_success_without_download(self):
        self.date.return_value = "01.07.2026"
        self.assertEqual(fc.main([]), 0)
        self.assertEqual(self.calls, [])
        self.assertEqual(self.date.call_count, 6)
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8")), self.initial)

    def test_explicit_cli_enables_inactive_and_overrides_environment(self):
        with patch.dict(os.environ, {"FEDSTAT_ONLY_IDS": "59449"}):
            self.assertEqual(fc.main(["--only=33648,61497", "--force"]), 0)
        self.assert_sources(["33648", "61497"])

    def test_environment_enables_inactive(self):
        with patch.dict(os.environ, {"FEDSTAT_ONLY_IDS": "33575"}):
            self.assertEqual(fc.main([]), 0)
        self.assert_sources(["33575"])

    def test_aliases_expand_both_parts_in_explicit_order(self):
        self.assertEqual(fc.main(["--only=31074;34118"]), 0)
        self.assert_sources(["31074_часть1", "31074_часть2", "34118_часть1", "34118_часть2"])

    def test_unknown_cli_and_environment_fail_before_any_work(self):
        for args, env in [(["--only=33648,typo"], ""), ([], "33648,typo")]:
            with self.subTest(args=args), patch.dict(os.environ, {"FEDSTAT_ONLY_IDS": env}):
                self.assertEqual(fc.main(args), 2)
        self.create.assert_not_called()
        self.assertFalse(self.downloads.exists())
        self.assertEqual(json.loads(self.state_path.read_text(encoding="utf-8")), self.initial)

    def test_partial_failure_keeps_failed_and_inactive_dates_and_receipts(self):
        self.failed = {"34118_часть2"}
        self.assertEqual(fc.main([]), 2)
        state = json.loads(self.state_path.read_text(encoding="utf-8"))
        for src in self.initial:
            updated = src in fc.DEFAULT_INDICATOR_USAGE and src not in self.failed
            self.assertEqual(state[src], "02.07.2026" if updated else self.initial[src])
            self.assertEqual(receipt_path(self.downloads / f"{src}.xls").exists(), updated)

    def test_typo_in_default_configuration_fails_without_partial_run(self):
        with patch.object(fc, "DEFAULT_INDICATOR_USAGE", {"57824": "salary", "typo": "bad"}):
            self.assertFalse(fc.run()[1])
        self.create.assert_not_called()
        self.assertFalse(self.downloads.exists())
        self.assertIn("DEFAULT_INDICATOR_USAGE: typo", self.output.getvalue())

    def test_empty_default_configuration_fails_without_browser_or_writes(self):
        with patch.object(fc, "DEFAULT_INDICATOR_USAGE", {}):
            self.assertFalse(fc.run()[1])
        self.create.assert_not_called()
        self.assertFalse(self.downloads.exists())


if __name__ == "__main__":
    unittest.main()
