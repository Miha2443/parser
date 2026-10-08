from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from backend.profile_service import FilterUnavailable
from backend.updates_service import UpdatesService
from pipeline.data_access import DataContext


class UpdatesTests(unittest.TestCase):
    def test_missing_sources_and_period_validation(self):
        with TemporaryDirectory() as directory:
            service = UpdatesService(DataContext(Path(directory)))
            report = service.report()
            self.assertEqual(report["events"], [])
            self.assertEqual(report["marts"], [])
            with self.assertRaises(FilterUnavailable):
                service.report(days=100)

    def test_last_run_filters_monitoring_and_read_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / "data/processed"
            processed.mkdir(parents=True)
            now = datetime.now().isoformat()
            rows = [{"run_id": "one", "ts": now, "indicator": "salary", "status": "success", "rows": 5, "new_date": "2026"},
                    {"run_id": "one", "ts": now, "indicator": "ipc", "status": "error", "error": "fixture"},
                    {"run_id": "one", "ts": now, "indicator": "_run", "status": "finished", "duration_sec": 3}]
            audit = processed / "etl_audit.jsonl"
            audit.write_text("\n".join(json.dumps(row) for row in rows))
            changes = processed / "monitoring_2_0_changes.jsonl"
            changes.write_text(json.dumps({"event_id": "one", "detected_at": now,
                                            "added": [{"uin": "1", "address": "Test"}], "removed": []}))
            before = {path: path.read_bytes() for path in (audit, changes)}
            service = UpdatesService(DataContext(root))
            report = service.report(only_errors=True)
            self.assertEqual(report["lastRun"]["success"], 1)
            self.assertEqual(report["lastRun"]["error"], 1)
            self.assertEqual(len(report["events"]), 1)
            self.assertEqual(len(report["indicators"]), 2)
            self.assertEqual(report["latestMonitoring"]["added"], 1)
            self.assertEqual(len(report["monitoringChanges"]), 1)
            json.dumps(report, allow_nan=False)
            for path, content in before.items():
                self.assertEqual(path.read_bytes(), content)


if __name__ == "__main__":
    unittest.main()
