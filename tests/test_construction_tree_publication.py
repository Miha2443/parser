"""Exercise the real publication hooks with collectors/builds stubbed only."""
from contextlib import ExitStack
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from docker import publication
from pipeline import construction_tree_history as history
from tests.test_construction_tree_history import stamp, tree_book


class TreePublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.working = self.root / 'work'
        self.runtime = self.root / 'runtime'
        self.runtime.mkdir()
        self.stack = ExitStack()
        self.addCleanup(self.stack.close)
        # Exercise history on Windows too. POSIX symlink atomicity is covered by
        # Docker publication tests; this substitute changes only the live pointer.
        self.live = ''
        self.stack.enter_context(patch.object(publication, 'live_target', side_effect=lambda runtime: self.live))
        def swap(runtime, target):
            publication.require_target(runtime, target)
            self.live = target
        self.stack.enter_context(patch.object(publication, 'swap', side_effect=swap))
        for name in ['build_candidate_processed', 'build_candidate', 'validate']:
            self.stack.enter_context(patch.object(publication, name))
        self.stack.enter_context(patch.object(publication, 'select_candidate_erz', return_value={'selection': 'current', 'reason': 'fixture'}))
        self.stack.enter_context(patch.object(publication, 'publication_status', return_value={'optional_sources': {}}))
        self.stack.enter_context(patch('docker.mortgage.select_source', return_value={}))
        self.stack.enter_context(patch('docker.preferential.select_source', return_value={}))
        self.stack.enter_context(patch('docker.preferential.verify_snapshot'))
        self.clock = self.stack.enter_context(patch.object(history, '_now', return_value=stamp('2026-10-30T17:00:00')))

    def publish(self, total):
        tree_book(self.working, total)
        publication.publish(self.working, self.runtime)
        return self.runtime / publication.live_target(self.runtime)

    def test_release_copy_live_route_retry_failure_rollback_and_ack_preserve_baseline(self):
        friday = self.publish(51)
        # Pending bootstrap is not a successful publication baseline yet.
        self.assertNotIn('baseline', history._journal(self.runtime))
        publication.transaction(self.runtime, 'ack')
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        with patch.object(publication, 'validate', side_effect=RuntimeError('candidate failed')):
            with self.assertRaises(RuntimeError):
                self.publish(999)
        self.assertEqual(self.runtime / self.live, friday)
        self.assertFalse((self.runtime / 'pending.json').exists())
        captured = history._journal(self.runtime)['snapshots']['2026-11-01']
        self.assertEqual(captured['metrics'][0]['value'], 51)
        monday_failed_readiness = self.publish(77)
        self.assertEqual(history.read_history(monday_failed_readiness)['snapshots']['2026-11-01'], captured)
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)
        publication.transaction(self.runtime, 'rollback')
        publication.transaction(self.runtime, 'ack')
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)
        monday = self.publish(88)
        publication.transaction(self.runtime, 'ack')
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 88)
        self.assertEqual(history.read_history(monday)['snapshots']['2026-11-01'], captured)
        # Real history routes read the copied archive from the pinned release.
        from backend.construction_tree_routes import create_router
        from pipeline.data_access import DataContext
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        app = FastAPI()
        app.include_router(create_router(DataContext(monday), lambda fn, *args, **kw: fn(*args)))
        response = TestClient(app).get('/api/v1/construction-tree/history/2026-11-01')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), captured)
        self.assertEqual(TestClient(app).get('/api/v1/construction/tree').json()['metrics'][0]['value'], 88)
        self.clock.return_value = stamp('2026-11-09T07:00:00')
        next_week = self.publish(100)
        publication.transaction(self.runtime, 'ack')
        rows = history.read_history(next_week)['snapshots']
        self.assertEqual(rows['2026-11-01'], captured)
        self.assertEqual(rows['2026-11-08']['metrics'][0]['value'], 88)
        from docker.release_history import archive_releases
        archive_releases(self.runtime, keep=2, probe=lambda **kwargs: None)
        self.assertFalse(friday.exists())
        self.assertEqual(history.read_history(next_week)['snapshots']['2026-11-01'], captured)

    def test_offline_import_does_not_adopt_imported_history_and_legacy_release_has_no_fake_baseline(self):
        legacy = self.runtime / 'releases/legacy'
        legacy.mkdir(parents=True)
        self.live = 'releases/legacy'
        (self.runtime / 'previous-good.json').write_text(json.dumps({'current': 'releases/legacy', 'previous': ''}))
        tree_book(self.working, 51)
        imported = self.working / history.DIRECTORY
        imported.mkdir(parents=True)
        (imported / 'history.json').write_text('{"forged": true}')
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        monday = self.publish(77)
        self.assertEqual(history.read_history(monday)['snapshots'], {})
        publication.transaction(self.runtime, 'ack')
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 77)

    def test_precollector_hook_closes_old_publication_before_mutable_download_changes(self):
        self.publish(51)
        publication.transaction(self.runtime, 'ack')
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        publication.run_action('prepare-tree-history', self.working, self.runtime)
        captured = history._journal(self.runtime)['snapshots']['2026-11-01']
        tree_book(self.working, 999)
        self.assertEqual(captured['metrics'][0]['value'], 51)
        shell = (Path(__file__).resolve().parents[1] / 'docker/update.sh').read_text()
        start = shell.index('event updater_start "mode=$mode"')
        self.assertLess(shell.index('control prepare-tree-history', start), shell.index('if [[ "$mode" == --publish-existing ]]', start))

    def failed_ack_after_tree(self):
        previous = self.publish(51)
        publication.transaction(self.runtime, 'ack')
        self.clock.return_value = stamp('2026-10-31T12:00:00')
        rejected = self.publish(99)
        with patch('docker.release_history._write_record', side_effect=OSError('previous-good write failed')):
            with self.assertRaisesRegex(OSError, 'previous-good'):
                publication.transaction(self.runtime, 'ack')
        # Verify the injection really occurs after the new tree has been written.
        physical = json.loads((self.runtime / history.JOURNAL).read_text())
        self.assertEqual(physical['baseline']['release'], self.live)
        self.assertEqual(physical['baseline']['payload']['metrics'][0]['value'], 99)
        self.assertTrue((self.runtime / 'pending.json').exists())
        return previous, rejected

    def test_partial_tree_ack_previous_good_failure_rollback_ack_monday_uses_a51_not_b99(self):
        previous, rejected = self.failed_ack_after_tree()
        original = (previous / history.DIRECTORY / 'publication.json').read_bytes()
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)
        publication.transaction(self.runtime, 'rollback')
        publication.transaction(self.runtime, 'rollback')
        publication.transaction(self.runtime, 'ack')
        publication.transaction(self.runtime, 'ack')
        self.assertEqual(self.runtime / self.live, previous)
        self.assertNotEqual(self.runtime / self.live, rejected)
        self.assertEqual((previous / history.DIRECTORY / 'publication.json').read_bytes(), original)
        physical = json.loads((self.runtime / history.JOURNAL).read_text())
        self.assertEqual(physical['baseline']['payload']['metrics'][0]['value'], 51)
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        snapshot = history.prepare(self.runtime)['snapshots']['2026-11-01']
        self.assertEqual(snapshot['metrics'][0]['value'], 51)
        self.assertEqual(snapshot['snapshot']['publishedAt'], stamp('2026-10-30T17:00:00').isoformat())

    def test_partial_ack_retry_on_monday_closes_a_then_commits_b_with_real_retry_date(self):
        self.failed_ack_after_tree()
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        publication.transaction(self.runtime, 'ack')
        committed = history._journal(self.runtime)
        self.assertEqual(committed['snapshots']['2026-11-01']['metrics'][0]['value'], 51)
        self.assertEqual(committed['baseline']['payload']['metrics'][0]['value'], 99)
        self.assertEqual(committed['baseline']['publishedAt'], self.clock.return_value.isoformat())
        before_retry = (self.runtime / history.JOURNAL).read_bytes()
        publication.transaction(self.runtime, 'ack')
        self.assertEqual((self.runtime / history.JOURNAL).read_bytes(), before_retry)
        self.clock.return_value = stamp('2026-11-09T07:00:00')
        self.assertEqual(history.prepare(self.runtime)['snapshots']['2026-11-08']['metrics'][0]['value'], 99)

    def test_pending_clear_failure_and_lost_journal_recover_a_even_if_previous_good_already_b(self):
        previous = self.publish(51)
        publication.transaction(self.runtime, 'ack')
        self.clock.return_value = stamp('2026-10-31T12:00:00')
        self.publish(99)
        pending = self.runtime / 'pending.json'
        unlink = Path.unlink
        def fail_clear(path, *args, **kwargs):
            if path == pending:
                raise OSError('pending clear failed')
            return unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', fail_clear):
            with self.assertRaisesRegex(OSError, 'pending clear'):
                publication.transaction(self.runtime, 'ack')
        reserve = self.runtime / 'previous-good.json'
        self.assertEqual(json.loads(reserve.read_text())['current'], self.live)
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)
        (self.runtime / history.JOURNAL).unlink()
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)
        publication.transaction(self.runtime, 'rollback')
        with patch('docker.release_history._write_record', side_effect=OSError('reserve repair failed')):
            with self.assertRaisesRegex(OSError, 'reserve repair'):
                publication.transaction(self.runtime, 'ack')
        self.assertTrue(pending.exists())
        self.assertEqual(self.runtime / self.live, previous)
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)
        publication.transaction(self.runtime, 'ack')
        self.assertFalse(pending.exists())
        self.assertEqual(self.runtime / json.loads(reserve.read_text())['current'], previous)
        (self.runtime / history.JOURNAL).unlink()
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        self.assertEqual(history.prepare(self.runtime)['snapshots']['2026-11-01']['metrics'][0]['value'], 51)

    def test_rollback_tree_journal_write_failure_keeps_pending_and_can_be_retried(self):
        previous, _ = self.failed_ack_after_tree()
        write = history._write
        def fail_restore(path, value):
            if path == self.runtime / history.JOURNAL:
                raise OSError('restore journal failed')
            return write(path, value)
        with patch.object(history, '_write', side_effect=fail_restore):
            with self.assertRaisesRegex(OSError, 'restore journal'):
                publication.transaction(self.runtime, 'rollback')
        self.assertEqual(self.runtime / self.live, previous)
        self.assertTrue((self.runtime / 'pending.json').exists())
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        # Even before the failed restore is retried, the provisional B is ineligible.
        self.assertEqual(history.prepare(self.runtime)['snapshots']['2026-11-01']['metrics'][0]['value'], 51)
        publication.transaction(self.runtime, 'rollback')
        publication.transaction(self.runtime, 'ack')
        self.assertEqual(history._journal(self.runtime)['baseline']['payload']['metrics'][0]['value'], 51)

    def test_recovery_can_reactivate_next_after_failed_ack_and_rollback_without_backdating_b(self):
        self.failed_ack_after_tree()
        publication.transaction(self.runtime, 'rollback')
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        publication.transaction(self.runtime, 'activate-next')
        publication.transaction(self.runtime, 'ack')
        committed = history._journal(self.runtime)
        self.assertEqual(committed['snapshots']['2026-11-01']['metrics'][0]['value'], 51)
        self.assertEqual(committed['baseline']['payload']['metrics'][0]['value'], 99)
        self.assertEqual(committed['baseline']['publishedAt'], self.clock.return_value.isoformat())

    def test_failed_bootstrap_ack_has_no_eligible_baseline_until_transaction_commits(self):
        self.publish(99)
        with patch('docker.release_history._write_record', side_effect=OSError('reserve failed')):
            with self.assertRaises(OSError):
                publication.transaction(self.runtime, 'ack')
        self.assertNotIn('baseline', history._journal(self.runtime))
        self.clock.return_value = stamp('2026-11-02T07:00:00')
        self.assertEqual(history.prepare(self.runtime)['snapshots'], {})
        publication.transaction(self.runtime, 'ack')
        self.assertEqual(history._journal(self.runtime)['snapshots'], {})
        self.assertEqual(history._journal(self.runtime)['baseline']['publishedAt'], self.clock.return_value.isoformat())


if __name__ == '__main__':
    unittest.main()
