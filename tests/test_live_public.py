"""Offline contracts for the separate real-network runner, using test-only data."""
from datetime import datetime
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from docker.tests import live_public as live

REPO = Path(__file__).resolve().parents[1]


class LivePublicTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from deploy.linux.tests import smoke
        from pipeline.data_access import DataAccess, DataContext, NA_INDICATORS
        from pipeline.download_provenance import record_download
        from pipeline.registry import by_id
        cls.temp = tempfile.TemporaryDirectory()
        cls.addClassCleanup(cls.temp.cleanup)
        cls.seed = Path(cls.temp.name) / "seed"
        with patch.object(smoke, "ROOT", cls.seed):
            smoke.seed("TEST-ONLY PUBLIC CONTRACT")
        access = DataAccess(DataContext(cls.seed, downloads=cls.seed / "downloads"))
        for identifier in NA_INDICATORS:
            indicator = by_id(identifier)
            files = access._raw_files([access.DOWNLOADS_DIR], list(indicator.file_patterns))
            for file in files:
                record_download(file, source_id=indicator.source_ids[0])
        cls.build(cls.seed)
        cls.started = min(file.stat().st_mtime for file in (cls.seed / "downloads").glob("*.xls*")) - 2

    @staticmethod
    def build(root):
        environment = {**os.environ, "PARSER_ROOT": str(root), "PARSER_DOWNLOADS": str(root / "downloads"), "PYTHONUTF8": "1"}
        result = subprocess.run([sys.executable, "-m", "pipeline.build_realty_marts", "--strict"], cwd=REPO, env=environment,
                                capture_output=True, encoding="utf-8")
        if result.returncode:
            raise AssertionError(result.stdout + result.stderr)

    def setUp(self):
        self.temp = self.enterContext(tempfile.TemporaryDirectory())
        self.root = Path(self.temp) / "work"
        shutil.copytree(self.seed, self.root, copy_function=shutil.copy2)

    def access(self):
        from pipeline.data_access import DataAccess, DataContext
        return DataAccess(DataContext(self.root, downloads=self.root / "downloads", use_marts=True, require_marts=True))

    def status(self):
        return dict(run_id="fresh", status="success", current_stage="finished", marts_ok=True, processed_ok=True,
                    sources_requested=sorted(live.PUBLIC_SOURCES), successes=sorted(live.PUBLIC_SOURCES),
                    failures=[], status_write_errors=[], error="",
                    started_at=datetime.fromtimestamp(self.started).isoformat(),
                    finished_at=datetime.now().isoformat())

    def test_shared_public_checks_and_all_eight_positive_gate_remain_valid(self):
        from docker.coverage import validate_sources
        report = live.validate_public_sources(self.access(), self.started)
        self.assertEqual(len(report["downloads"]), 12)
        self.assertEqual(len(report["economics"]), 8)
        live.validate_status(self.status(), self.started)
        validate_sources(self.access())

    def test_missing_receipt_processed_file_or_partial_quart_is_rejected(self):
        from pipeline.download_provenance import receipt_path
        import pandas as pd
        receipt = next((self.root / "downloads/.download_receipts").glob("*.json"))
        saved = receipt.read_bytes()
        receipt.unlink()
        with self.assertRaisesRegex(RuntimeError, "missing fresh verified"):
            live.validate_receipts(self.access(), self.started)
        receipt.write_bytes(saved)
        processed = self.root / "data/processed/avg_salary.pkl"
        content = processed.read_bytes()
        processed.unlink()
        with self.assertRaises(RuntimeError):
            live.validate_public_sources(self.access(), self.started)
        processed.write_bytes(content)
        os.utime(processed, (time.time(), time.time()))
        mart = self.root / "data/marts/realty/kvartirografia.pkl"
        payload = pd.read_pickle(mart)
        payload["apartments_per_dev"] = payload["apartments_per_dev"].loc[lambda frame: frame.region_key.eq("rf")]
        pd.to_pickle(payload, mart)
        with self.assertRaises(RuntimeError):
            live.validate_public_sources(self.access(), self.started)

    def test_stale_incomplete_and_duplicate_source_status_cannot_pass(self):
        for changes in ({"started_at": "2000-01-01T00:00:00"}, {"run_id": "previous"},
                        {"successes": ["monitoring"]}, {"successes": list(live.PUBLIC_SOURCES) + ["fedstat"]},
                        {"current_stage": "running"}, {"processed_ok": False}, {"failures": ["rosstat"]}):
            with self.subTest(changes=changes), self.assertRaises(RuntimeError):
                live.validate_status({**self.status(), **changes}, self.started, previous_id="previous")

    def test_duplicate_housing_history_cannot_pass_full_production_coverage(self):
        import pandas as pd
        from docker.coverage import validate_sources
        source = next((self.root / "data/raw/realty/nashdom").glob("monitoring_*.xlsx"))
        history = pd.read_excel(source, sheet_name="Данные с 2011 года", header=None)
        history = pd.concat([history, history.iloc[[3]]], ignore_index=True)
        with pd.ExcelWriter(source, mode="a", engine="openpyxl", if_sheet_exists="replace") as writer:
            history.to_excel(writer, sheet_name="Данные с 2011 года", header=False, index=False)
        self.build(self.root)
        with self.assertRaisesRegex(ValueError, "duplicate year/month"):
            validate_sources(self.access())

    def test_public_runner_uses_the_same_mandatory_housing_contract(self):
        from openpyxl import load_workbook
        source = next((self.root / "data/raw/realty/nashdom").glob("monitoring_*.xlsx"))
        workbook = load_workbook(source)
        workbook["Данные с 2011 года"]["G3"] = "Нет месяца"
        workbook.save(source)
        workbook.close()
        self.build(self.root)
        with self.assertRaisesRegex(RuntimeError, "operational housing monthly history is empty"):
            live.validate_public_sources(self.access(), self.started)

    def test_six_public_sources_are_explicitly_denied_full_production_publication(self):
        for file in (self.root / "data/raw/realty/erzrf").glob("*"):
            file.unlink()
        self.build(self.root)
        live.validate_public_sources(self.access(), self.started)
        denial = live.confirm_full_publication_denied(self.access(), self.status(), self.started)
        self.assertEqual(set(denial), {"status_gate", "coverage_gate"})
        self.assertIn("erzrf", denial["coverage_gate"])

    def test_public_api_pages_use_real_service_contracts_and_source_defaults(self):
        from backend.app import create_app
        from fastapi.testclient import TestClient
        with patch.dict(os.environ, {"PARSER_ROOT": str(self.root), "PARSER_DOWNLOADS": str(self.root / "downloads"),
                                     "PARSER_REQUIRE_REALTY_MARTS": "1"}), TestClient(create_app()) as client:
            def get(route):
                response = client.get(route)
                self.assertEqual(response.status_code, 200, route)
                return response.json()
            defaults = live.validate_public_pages(get, self.access().load_monitoring_operational_history()["housing_monthly"])
        self.assertEqual(defaults["salary"]["period"], "month")
        self.assertIn("Моск", defaults["salary"]["region"])

    def test_runner_refuses_the_host_workspace(self):
        with self.assertRaises(RuntimeError):
            live.guard(REPO)

    def test_fresh_economics_audit_must_confirm_each_processed_indicator(self):
        from pipeline.data_access import NA_INDICATORS
        entries = [{"run_id": "etl-fresh", "ts": datetime.now().isoformat(), "indicator": identifier,
                    "status": "success", "rows": len(self.access().load_indicator(identifier)),
                    "files": sorted(set(self.access().load_indicator(identifier).source_file.astype(str)))}
                   for identifier in ("avg_salary", "ipc", *NA_INDICATORS)]
        entries.append({"run_id": "etl-fresh", "ts": datetime.now().isoformat(), "indicator": "_run",
                        "status": "finished", "success": 8, "skip": 0, "error": 0})
        audit = self.root / "data/processed/etl_audit.jsonl"
        def write(records):
            audit.write_text("\n".join(json.dumps(entry) for entry in records), encoding="utf-8")
        write(entries)
        self.assertEqual(len(live.validate_etl_audit(self.access(), self.started)), 8)
        for first in ({"status": "skip"}, {"rows": 0}, {"files": []}, {"ts": "2000-01-01T00:00:00"}):
            write([{**entries[0], **first}, *entries[1:]])
            with self.subTest(first=first), self.assertRaises(RuntimeError):
                live.validate_etl_audit(self.access(), self.started)

    def test_historical_api_ytd_cannot_hide_duplicate_or_unrelated_source_measurements(self):
        import pandas as pd
        history = pd.DataFrame([{"year": 2011, "month": 1, "value_thousand_m2": 100},
                                {"year": 2011, "month": 2, "value_thousand_m2": 200}])
        report = {"selection": {"month": 2}, "currentYear": 2026,
                  "tables": [{"id": "housing", "rows": [{"Год": 2011, "За выбранный период, млн м²": .3},
                                                           {"Год": 2012, "За выбранный период, млн м²": None}]}]}
        live.validate_housing_ytd(report, history)
        with self.assertRaisesRegex(RuntimeError, "duplicate"):
            live.validate_housing_ytd(report, pd.concat([history, history]))
        for row_index, value in ((0, 10.22), (1, 0)):
            changed = json.loads(json.dumps(report))
            changed["tables"][0]["rows"][row_index]["За выбранный период, млн м²"] = value
            with self.subTest(row=row_index), self.assertRaises(RuntimeError):
                live.validate_housing_ytd(changed, history)


if __name__ == "__main__":
    unittest.main()
