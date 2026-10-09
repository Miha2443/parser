"""A fast build must pass publication without hiding later source changes."""
import contextlib
from datetime import datetime
import io
import os
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch

import pandas as pd
from pipeline import build_realty_marts as builder


class MartFreshnessTests(unittest.TestCase):
    def test_source_and_build_in_same_second_and_later_source_change(self):
        completed = datetime(2026, 10, 8, 14, 10, 59, 700000)

        class FixedClock(datetime):
            @classmethod
            def now(cls):
                return completed

        with tempfile.TemporaryDirectory() as directory, contextlib.ExitStack() as stack:
            root = Path(directory)
            raw = root / "source.csv"
            raw.write_text("value\n1\n", encoding="utf-8")
            downloaded = completed.replace(microsecond=442919).timestamp()
            os.utime(raw, (downloaded, downloaded))
            marts = root / "marts"
            access = SimpleNamespace(load_fixture=lambda: pd.DataFrame({"value": [1]}))
            for name, value in {"ROOT": root, "MART_DIR": marts, "MANIFEST": marts / "manifest.json",
                                "datetime": FixedClock, "_prepare_imports": lambda: access,
                                "_specs": lambda _: [builder.MartSpec("fixture", "load_fixture", lambda _: [raw])]}.items():
                stack.enter_context(patch.object(builder, name, value))
            stack.enter_context(contextlib.redirect_stdout(io.StringIO()))
            self.assertEqual(builder.build(strict=True), 0)
            self.assertEqual(builder.check_manifest(strict=True), 0,
                             "A file downloaded before the build in the same second is fresh")
            modified = completed.replace(microsecond=900000).timestamp()
            os.utime(raw, (modified, modified))
            self.assertEqual(builder.check_manifest(strict=True), 1,
                             "A file modified after the build in the same second is stale")
