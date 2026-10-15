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

    def test_outage_and_retained_data_are_reported_separately_and_read_only(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            processed = root / 'data/processed'
            processed.mkdir(parents=True)
            status = {'status': 'degraded', 'updated_at': datetime.now().isoformat(),
                      'source_outcomes': {
                          'fedstat': {'status': 'skipped', 'reason_code': 'site_unavailable',
                                      'reason': 'Сайт недоступен: HTTP 503', 'checked_at': '2026-10-15T06:01:00'},
                          'monitoring': {'status': 'success', 'checked_at': '2026-10-15T06:02:00'},
                          'erz-cards': {'status': 'success', 'reason': 'Источник обновлён'},
                          'erz-top': {'status': 'skipped', 'reason_code': 'maintenance',
                                      'reason': 'На сайте проводятся технические работы'}}}
            publication = {'sources': {'fedstat': {'state': 'retained'}, 'monitoring': {'state': 'fresh'},
                                       'erz-top': {'state': 'unavailable'},
                                       'erz-cards': {'state': 'retained', 'reason_code': 'provider_maintenance',
                                                     'reason': 'Сохранён комплект ЕРЗ: техработы TOP'}}}
            status_path = processed / 'realty_update_status.json'
            selected_path = processed / 'publication_status.json'
            status_path.write_text(json.dumps(status))
            selected_path.write_text(json.dumps(publication))
            before = {path: (path.read_bytes(), path.stat().st_mtime_ns) for path in (status_path, selected_path)}
            report = UpdatesService(DataContext(root)).report()
            sources = {row['source']: row for row in report['sourceUpdates']}
            self.assertEqual(report['realtySummary']['label'], 'завершено с пропусками')
            self.assertEqual(sources['fedstat']['status'], 'Пропущен')
            self.assertEqual(sources['fedstat']['dataState'], 'Сохранены прежние')
            self.assertEqual(sources['fedstat']['reason'], 'Сайт недоступен: HTTP 503')
            self.assertEqual(sources['monitoring']['dataState'], 'Обновлены или проверены')
            self.assertEqual(sources['erz-top']['dataState'], 'Данных пока нет')
            self.assertEqual(sources['erz-cards']['status'], 'Проверен')
            self.assertEqual(sources['erz-cards']['reason'], 'Сохранён комплект ЕРЗ: техработы TOP')
            for path, expected in before.items():
                self.assertEqual((path.read_bytes(), path.stat().st_mtime_ns), expected)


if __name__ == "__main__":
    unittest.main()
