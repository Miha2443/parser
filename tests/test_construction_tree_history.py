"""Weekly snapshots come only from acknowledged, served tree payloads."""
from datetime import datetime
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient
from openpyxl import Workbook

from backend.app import create_app
from backend.tests.test_construction_tree import ROWS
from backend.construction_tree_service import SHEET, COLUMN, ConstructionTreeService
from backend.profile_service import SourceUnavailable
from pipeline import construction_tree_history as history
from pipeline.data_access import DataContext


def stamp(value):
    return datetime.fromisoformat(value + '+03:00')


def tree_book(root, total, sector='03 ДОУ'):
    path = root / 'data/raw/realty/nashdom/monitoring_2_0_20261030.xlsx'
    path.parent.mkdir(parents=True, exist_ok=True)
    book = Workbook()
    sheet = book.active
    sheet.title = SHEET
    sheet['I1'] = COLUMN
    for row, identifier, label, value in ROWS:
        sheet.cell(row, 8, label)
        sheet.cell(row, 9, total if identifier == 'total' else value)
    registry = book.create_sheet('Реестр ОКС')
    registry.append(['Назначение', 'Статус объекта', 'Статус РС', 'Общая площадь', 'ОСМ код'])
    registry.append(['Нежилье', 'Строящийся', 'Действует', ROWS[7][3] * 1_000_000, sector])
    book.save(path)
    book.close()


class TreeHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.release = self.runtime / 'releases/friday'
        self.book(self.release, 51)
        history.stage(self.release, self.runtime)

    book = staticmethod(tree_book)

    def acknowledge(self, target='releases/friday', when='2026-10-30T17:00:00'):
        history.acknowledge(self.runtime, target, stamp(when))

    def prepare(self, when='2026-11-02T07:00:00'):
        return history.prepare(self.runtime, stamp(when))

    def test_bootstrap_never_archives_unpublished_import_or_invents_backfill(self):
        self.assertEqual(self.prepare()['snapshots'], {})
        self.acknowledge(when='2026-11-02T08:00:00')
        self.assertEqual(self.prepare('2026-11-02T09:00:00')['snapshots'], {})

    def test_daily_monday_retries_and_next_monday_preserve_exact_published_payload(self):
        self.acknowledge()
        self.assertEqual(self.prepare('2026-10-31T07:00:00')['snapshots'], {})
        baseline = history.read_current(self.release)
        # Newer mutable monitoring must have no influence on the archive.
        self.book(self.release, 999)
        with patch.object(ConstructionTreeService, 'report', side_effect=AssertionError('Must not reconstruct history')):
            first = self.prepare()
        snapshot = first['snapshots']['2026-11-01']
        self.assertEqual(snapshot['nonresidentialSectors']['status'], 'available')
        self.assertEqual(snapshot['nonresidentialSectors']['items'][0]['label'], '03 ДОУ')
        self.assertEqual({k: v for k, v in snapshot.items() if k != 'snapshot'}, baseline)
        self.assertEqual(snapshot['snapshot']['publishedAt'], stamp('2026-10-30T17:00:00').isoformat())
        self.assertFalse(snapshot['snapshot']['delayedCapture'])
        original = (self.runtime / history.JOURNAL).read_bytes()
        for day in ['2026-11-02T09:00:00', '2026-11-03T07:00:00', '2026-11-08T20:00:00']:
            self.prepare(day)
            self.assertEqual((self.runtime / history.JOURNAL).read_bytes(), original)
        monday = self.runtime / 'releases/monday'
        self.book(monday, 77, '04 Школы')
        history.stage(monday, self.runtime)
        self.acknowledge('releases/monday', '2026-11-02T09:30:00')
        second = self.prepare('2026-11-09T07:00:00')
        self.assertEqual(set(second['snapshots']), {'2026-11-01', '2026-11-08'})
        self.assertEqual(second['snapshots']['2026-11-01'], snapshot)
        self.assertEqual(second['snapshots']['2026-11-08']['metrics'][0]['value'], 77)
        self.assertEqual(second['snapshots']['2026-11-08']['nonresidentialSectors']['items'][0]['label'], '04 Школы')
        self.assertEqual(second['snapshots']['2026-11-01']['nonresidentialSectors']['items'][0]['label'], '03 ДОУ')

    def test_monday_moscow_boundary_not_utc_date(self):
        self.acknowledge()
        before = history.prepare(self.runtime, datetime.fromisoformat('2026-11-01T20:59:59+00:00'))
        self.assertEqual(before['snapshots'], {})
        after = history.prepare(self.runtime, datetime.fromisoformat('2026-11-01T21:00:00+00:00'))
        self.assertIn('2026-11-01', after['snapshots'])

    def test_sunday_build_acknowledged_monday_cannot_erase_pre_monday_baseline(self):
        self.acknowledge()
        sunday = self.runtime / 'releases/sunday-build'
        self.book(sunday, 999, '04 Школы')
        history.stage(sunday, self.runtime)
        self.assertEqual(history.read_history(sunday)['snapshots'], {})
        self.acknowledge('releases/sunday-build', '2026-11-02T00:01:00')
        row = history.read_history(sunday)['snapshots']['2026-11-01']
        self.assertEqual(row['metrics'][0]['value'], 51)
        self.assertEqual(row['nonresidentialSectors']['items'][0]['label'], '03 ДОУ')
        self.assertEqual(history.read_current(sunday)['metrics'][0]['value'], 999)

    def test_skipped_monday_marks_capture_delayed_preserves_old_dates_no_missing_week_backfill(self):
        self.acknowledge()
        snapshots = self.prepare('2026-11-17T07:00:00')['snapshots']
        self.assertEqual(list(snapshots), ['2026-11-15'])
        row = snapshots['2026-11-15']
        self.assertTrue(row['snapshot']['delayedCapture'])
        self.assertEqual(row['snapshot']['publishedAt'], stamp('2026-10-30T17:00:00').isoformat())
        self.assertEqual(row['source'], history.read_current(self.release)['source'])

    def test_failed_or_unacknowledged_candidate_never_advances_baseline(self):
        self.acknowledge()
        before = self.prepare()['snapshots']['2026-11-01']
        failed = self.runtime / 'releases/failed'
        self.book(failed, 999)
        history.stage(failed, self.runtime)
        # Collection/build/readiness fail: no acknowledge call.
        after = self.prepare('2026-11-03T07:00:00')
        self.assertEqual(after['snapshots']['2026-11-01'], before)
        self.assertEqual(after['baseline']['release'], 'releases/friday')

    def test_atomic_failure_preserves_existing_journal_and_retry(self):
        self.acknowledge()
        original = (self.runtime / history.JOURNAL).read_bytes()
        with patch.object(history.os, 'replace', side_effect=OSError('disk unavailable')):
            with self.assertRaises(OSError):
                self.prepare()
        self.assertEqual((self.runtime / history.JOURNAL).read_bytes(), original)
        self.assertFalse(list(self.runtime.glob('*.tmp')))
        self.assertIn('2026-11-01', self.prepare()['snapshots'])

    def test_candidate_carries_archive_ignores_mutable_import_and_serves_frozen_current(self):
        self.acknowledge()
        first = self.prepare()['snapshots']
        imported = self.runtime / 'releases/imported'
        self.book(imported, 88)
        target = imported / history.DIRECTORY
        target.mkdir(parents=True)
        (target / 'history.json').write_text('{"forged": true}')
        (target / 'current.json').write_text('{"forged": true}')
        history.stage(imported, self.runtime)
        self.assertEqual(history.read_history(imported)['snapshots'], first)
        self.book(imported, 999)
        self.assertEqual(ConstructionTreeService(DataContext(imported)).report()['metrics'][0]['value'], 88)

    def test_recover_journal_from_acknowledged_release_not_failed_release_or_working_tree(self):
        self.acknowledge()
        self.prepare()
        monday = self.runtime / 'releases/monday'
        self.book(monday, 77)
        history.stage(monday, self.runtime)
        self.acknowledge('releases/monday', '2026-11-02T09:30:00')
        (self.runtime / 'previous-good.json').write_text(json.dumps({'current': 'releases/monday', 'previous': 'releases/friday'}))
        (self.runtime / history.JOURNAL).unlink()
        recovered = self.prepare('2026-11-09T07:00:00')
        self.assertEqual(set(recovered['snapshots']), {'2026-11-01', '2026-11-08'})
        self.assertEqual(recovered['snapshots']['2026-11-08']['metrics'][0]['value'], 77)

    def test_missing_current_does_not_invent_totals_or_reuse_an_outdated_baseline(self):
        self.acknowledge()
        empty = self.runtime / 'releases/empty'
        history.stage(empty, self.runtime)
        self.assertIsNone(history.read_current(empty))
        self.acknowledge('releases/empty', '2026-11-02T09:00:00')
        self.assertEqual(set(self.prepare('2026-11-09T07:00:00')['snapshots']), {'2026-11-01'})

    def test_history_api_csv_survive_absent_live_sources_are_date_specific_and_not_cached(self):
        self.acknowledge()
        self.prepare()
        candidate = self.runtime / 'releases/live'
        history.stage(candidate, self.runtime)
        context = DataContext(candidate, require_marts=True)
        profile = MagicMock(context=context)
        with patch('pipeline.data_access.DataContext.from_environment', return_value=context):
            app = create_app(profile, MagicMock(), MagicMock())
        with patch('pipeline.source_availability.unavailable', return_value={'monitoring', 'construction'}):
            client = TestClient(app)
            listing = client.get('/api/v1/construction-tree/history')
            self.assertEqual(listing.status_code, 200, listing.text)
            self.assertEqual(listing.json()['snapshots'][0]['date'], '2026-11-01')
            route = '/api/v1/construction-tree/history/2026-11-01'
            report = client.get(route)
            self.assertEqual(report.status_code, 200, report.text)
            self.assertEqual(report.headers['cache-control'], 'no-store')
            self.assertEqual(report.json()['metrics'][0]['value'], 51)
            csv = client.get(route + '/export')
            self.assertEqual(csv.status_code, 200)
            self.assertIn('2026-11-01', csv.text)
            self.assertIn(report.json()['version'], csv.text)
            self.assertIn('2026-10-30T17:00:00', csv.text)
            self.assertIn('03 ДОУ', csv.text)
            self.assertEqual(client.get('/api/v1/construction-tree/history/2026-11-08').status_code, 404)
            self.assertEqual(client.get('/api/v1/construction-tree/history/2026-02-30').status_code, 422)

    def test_corrupt_snapshot_is_rejected_not_silently_replaced(self):
        self.acknowledge()
        payload = self.prepare()
        payload['snapshots']['2026-11-01']['metrics'][0]['value'] = 999
        (self.runtime / history.JOURNAL).write_text(json.dumps(payload))
        with self.assertRaisesRegex(RuntimeError, 'checksum'):
            self.prepare()

    def test_corrupt_frozen_json_schema_or_checksum_is_unavailable_503_not_500(self):
        original = (self.release / history.DIRECTORY / 'current.json').read_text()
        for corruption in ('{broken', 'null', '[true]', '{"metrics": [true]}', original.replace('"value": 51', '"value": 999')):
            with self.subTest(corruption=corruption[:30]):
                (self.release / history.DIRECTORY / 'current.json').write_text(corruption)
                context = DataContext(self.release)
                profile = MagicMock(context=context)
                with patch('pipeline.data_access.DataContext.from_environment', return_value=context):
                    app = create_app(profile, MagicMock(), MagicMock())
                with self.assertLogs('backend.app', level='ERROR'):
                    response = TestClient(app).get('/api/v1/construction/tree')
                self.assertEqual(response.status_code, 503, response.text)
                self.assertNotIn('metrics', response.json())
        (self.release / history.DIRECTORY / 'current.json').write_text(original)
        for proof in ('{broken', '[]', '{"sha256": "wrong"}'):
            (self.release / history.DIRECTORY / 'current-proof.json').write_text(proof)
            with self.assertRaises(SourceUnavailable):
                ConstructionTreeService(DataContext(self.release)).report()
        (self.release / history.DIRECTORY / 'current.json').unlink()
        with self.assertRaises(SourceUnavailable):
            ConstructionTreeService(DataContext(self.release)).report()


if __name__ == '__main__':
    unittest.main()
