"""Only a failed export gets one fresh session; successful sources stay done."""
from contextlib import ExitStack, redirect_stdout
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import fedstat_checker as fc


class ExportRecoveryTests(unittest.TestCase):
    def run_case(self, export, date):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            root = Path(folder)
            first, second = Mock(), Mock()
            stack.enter_context(patch.multiple(fc, DOWNLOAD_DIR=root,
                INDICATORS={"one": "one", "two": "two"},
                DEFAULT_INDICATOR_USAGE={"one": "one", "two": "two"}))
            factory = stack.enter_context(patch.object(fc, "create_driver", side_effect=[first, second]))
            stack.enter_context(patch.object(fc, "load_state", return_value={"one": "old", "two": "old"}))
            saved = stack.enter_context(patch.object(fc, "save_state"))
            stack.enter_context(patch.object(fc, "_load_run_checkpoint", return_value={}))
            complete = stack.enter_context(patch.object(fc, "_mark_run_completed"))
            stack.enter_context(patch.object(fc, "retire_download"))
            stack.enter_context(patch.object(fc, "get_last_update_date", side_effect=date))
            download = stack.enter_context(patch.object(fc, "download_excel", side_effect=export))
            stack.enter_context(redirect_stdout(StringIO()))
            result = fc.run()
            return result, factory, download, saved, complete, first, second

    def test_only_failed_export_retries_once_with_reconfirmed_date(self):
        result, factory, download, saved, complete, old, fresh = self.run_case(
            [Path("one.xls"), None, Path("two.xls")], ["12.10.2026", "12.10.2026", "13.10.2026"])
        self.assertTrue(result[1])
        self.assertEqual([call.args[0] for call in download.call_args_list], ["one", "two", "two"])
        self.assertEqual(download.call_args_list[-1].kwargs["remote_date"], "13.10.2026")
        self.assertEqual(saved.call_args.args[0], {"one": "12.10.2026", "two": "13.10.2026"})
        self.assertEqual(factory.call_count, 2)
        self.assertEqual(complete.call_count, 2)
        old.quit.assert_called_once()
        fresh.quit.assert_called_once()

    def test_persistent_export_failure_keeps_old_date_and_returns_failure(self):
        result, factory, download, saved, complete, old, fresh = self.run_case(
            [Path("one.xls"), None, None], ["12.10.2026"] * 3)
        self.assertFalse(result[1])
        self.assertEqual(len(result[0]), 1)
        self.assertEqual(download.call_count, 3)
        self.assertEqual(saved.call_args.args[0], {"one": "12.10.2026", "two": "old"})
        self.assertEqual(complete.call_count, 1)
        old.quit.assert_called_once()
        fresh.quit.assert_called_once()

    def test_missing_recovery_date_never_attempts_unverified_export(self):
        result, factory, download, saved, complete, old, fresh = self.run_case(
            [Path("one.xls"), None], ["12.10.2026", "12.10.2026", None])
        self.assertFalse(result[1])
        self.assertEqual(download.call_count, 2)
        self.assertEqual(saved.call_args.args[0], {"one": "12.10.2026", "two": "old"})
        self.assertEqual(complete.call_count, 1)
        old.quit.assert_called_once()
        fresh.quit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
