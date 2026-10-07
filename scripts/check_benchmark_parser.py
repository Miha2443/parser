"""Offline benchmark regression checks on synthetic data in a temp directory."""
from __future__ import annotations

import csv
import importlib.util
import json
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest

sys.dont_write_bytecode = True
SCRIPT = Path(__file__).with_name("benchmark_parser.py")
spec = importlib.util.spec_from_file_location("benchmark_parser", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


class BenchmarkChecks(unittest.TestCase):
    def test_escape_is_rejected(self):
        with tempfile.TemporaryDirectory() as temporary:
            with self.assertRaises(ValueError):
                benchmark.within(Path(temporary), "../outside.pkl")

    def test_less_than_five_repetitions_is_rejected(self):
        with self.assertRaises(SystemExit) as caught:
            benchmark.main(["--repeats", "4"])
        self.assertEqual(caught.exception.code, 2)

    def test_guard_blocks_file_write_and_network(self):
        with tempfile.TemporaryDirectory() as temporary:
            sentinel = Path(temporary) / "sentinel.txt"
            sentinel.write_text("unchanged", encoding="utf-8")
            code = """
import importlib.util, json, pathlib, socket, sys
spec = importlib.util.spec_from_file_location('bench', sys.argv[1])
bench = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bench)
violations = bench.worker_guard()
for operation in (lambda: pathlib.Path(sys.argv[2]).write_text('changed'),
                  lambda: socket.getaddrinfo('localhost', 80)):
    try:
        operation()
    except PermissionError:
        pass
print(json.dumps(violations))
"""
            result = subprocess.run([sys.executable, "-B", "-c", code, str(SCRIPT), str(sentinel)],
                                    text=True, capture_output=True, check=True)
            self.assertEqual(sentinel.read_text(encoding="utf-8"), "unchanged")
            self.assertEqual([item[0] for item in json.loads(result.stdout)], ["open", "socket.getaddrinfo"])

    def test_full_run_on_synthetic_snapshot(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as temporary:
            fixture = Path(temporary) / "fixture"
            output = Path(temporary) / "reports"
            mart_dir = fixture / "data/marts/realty"
            mart_dir.mkdir(parents=True)
            (fixture / "app").mkdir()
            (fixture / "scripts").mkdir()
            shutil.copy2(SCRIPT, fixture / "scripts/benchmark_parser.py")
            (fixture / "app/data_access.py").write_text(
                "from pathlib import Path\nimport os\nimport pandas as pd\nimport streamlit as st\ncalls = 0\n"
                "@st.cache_data(show_spinner=False, ttl=300)\ndef load_escrow_manual():\n"
                "    global calls\n    calls += 1\n"
                "    if calls > 1:\n        raise RuntimeError('Warm call missed the memory cache')\n"
                "    assert Path(os.environ['PARSER_ROOT']) == Path(__file__).resolve().parent.parent\n"
                "    return pd.read_pickle(Path(os.environ['PARSER_ROOT']) / "
                "'data/marts/realty/escrow_manual.pkl')\n", encoding="utf-8")
            pd.DataFrame({"value": range(7)}).to_pickle(mart_dir / "escrow_manual.pkl")
            (mart_dir / "manifest.json").write_text(json.dumps({"marts": {"escrow_manual": {
                "file": "data/marts/realty/escrow_manual.pkl", "sources": []}}}), encoding="utf-8")
            before = {str(p): benchmark.sha256(p) for p in fixture.rglob("*") if p.is_file()}
            old_root = benchmark.ROOT
            try:
                benchmark.ROOT = fixture
                result = benchmark.main(["--marts", "escrow_manual", "--output-dir", str(output)])
            finally:
                benchmark.ROOT = old_root
            with (output / "baseline.csv").open(encoding="utf-8-sig") as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual(result, 0, rows)
            self.assertEqual(len(rows), 15)
            for mode in benchmark.MODES:
                group = [row for row in rows if row["mode"] == mode]
                self.assertEqual(len(group), 5)
                self.assertEqual({row["repetition"] for row in group}, {"1", "2", "3", "4", "5"})
                self.assertTrue(all(row["status"] == "ok" and row["dataframe_rows_sum"] == "7" for row in group))
            self.assertEqual(before, {str(p): benchmark.sha256(p) for p in fixture.rglob("*") if p.is_file()})
            env = json.loads((output / "baseline_environment.json").read_text(encoding="utf-8"))
            self.assertEqual(env["original_inputs_changed_during_run"], [])


if __name__ == "__main__":
    unittest.main()
