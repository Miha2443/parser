"""Unchanged downloads are successful; unverified inputs must not be green."""
from contextlib import ExitStack, redirect_stdout
import copy
from io import StringIO
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

import requests
import rosstat_checker as rs


class RosstatOutcomeTests(unittest.TestCase):
    def setUp(self):
        self.stack = self.enterContext(ExitStack())
        root = Path(self.stack.enter_context(tempfile.TemporaryDirectory()))
        self.stack.enter_context(redirect_stdout(StringIO()))
        self.stack.enter_context(patch.object(rs, "DOWNLOAD_DIR", root / "downloads"))
        self.pages = {"https://example.test/a": [{"key": "a", "match_any": ["book"]}],
                      "https://example.test/b": [{"key": "b", "match_any": ["book"]}]}
        self.state = {key: {"date": "30.10.2026", "filename": key + ".xlsx"} for key in ("a", "b")}
        self.stack.enter_context(patch.object(rs, "PAGE_SOURCES", self.pages))
        self.stack.enter_context(patch.object(rs, "load_state", return_value=self.state))
        self.save = self.stack.enter_context(patch.object(rs, "save_state"))
        self.stack.enter_context(patch.object(rs, "_new_session", return_value=Mock()))
        self.stack.enter_context(patch.object(rs, "confirmed_download", return_value=True))
        self.stack.enter_context(patch.object(rs, "retire_download"))
        self.stack.enter_context(patch.object(rs.time, "sleep"))
        self.get = self.stack.enter_context(patch.object(rs, "get_files_on_page", return_value=[
            {"href": "https://example.test/book.xlsx", "date": "30.10.2026", "titles": ["book"]}]))
        self.download = self.stack.enter_context(patch.object(rs, "download_file", return_value=True))

    def test_all_inputs_checked_and_unchanged_is_success_with_no_download(self):
        self.assertEqual(rs.run(), [])
        self.assertEqual(self.get.call_count, 2)
        self.download.assert_not_called()
        with patch.object(rs, "run", return_value=[]):
            self.assertEqual(rs.main(), 0)

    def test_unreachable_page_fails_source_but_still_checks_next_page(self):
        rows = self.get.return_value
        self.get.side_effect = [requests.ConnectTimeout("unavailable"), rows]
        original = copy.deepcopy(self.state)
        with self.assertRaisesRegex(rs.RosstatCollectionError, "a"):
            rs.run()
        self.assertEqual(self.get.call_count, 2)
        self.assertEqual(self.state, original)

    def test_missing_required_link_fails_source(self):
        self.get.side_effect = [[], self.get.return_value]
        with self.assertRaisesRegex(rs.RosstatCollectionError, "a"):
            rs.run()
        self.assertEqual(self.get.call_count, 2)

    def test_partial_download_does_not_hide_another_input_failure(self):
        self.get.return_value[0]["date"] = "04.11.2026"
        self.download.side_effect = [True, False]
        with self.assertRaisesRegex(rs.RosstatCollectionError, "b"):
            rs.run()
        self.assertEqual(self.state["a"]["date"], "04.11.2026")
        self.assertEqual(self.state["b"]["date"], "30.10.2026")

    def test_cli_returns_source_failure_exit_code(self):
        with patch.object(rs, "run", side_effect=rs.RosstatCollectionError("a, b")):
            self.assertEqual(rs.main(), 2)


if __name__ == "__main__":
    unittest.main()
