"""Real host-shell recovery decisions against a disposable daemon simulator."""
import json
import os
import re
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

from docker import publication as p

REPO = Path(__file__).resolve().parents[1]
BASH = shutil.which('bash') or next((str(path) for path in (
    Path(os.environ.get('ProgramFiles', 'C:/Program Files')) / 'Git/bin/bash.exe',
    Path(os.environ.get('LOCALAPPDATA', '')) / 'Programs/Git/bin/bash.exe') if path.is_file()), None)


@unittest.skipUnless(BASH, 'Image preservation contract needs Bash, not Docker')
class CodeImagePreservationTests(unittest.TestCase):
    def run_preservation(self, failure=''):
        source = (REPO / 'docker/update.sh').read_text(encoding='utf-8')
        function = re.search(r'^preserve_code_image\(\) \{\n.*?^\}\n', source, re.M | re.S).group(0)
        harness = '''set -euo pipefail
event() { printf '%s %s\\n' "$1" "${2:-}"; }
bounded() {
  shift
  if [[ "$1 $2" == 'docker inspect' ]]; then printf '%s\\n' 'sha256:previous-image'; return; fi
  if [[ "$1 $2 $3" != 'docker image tag' ]]; then return 99; fi
  case "$PRESERVE_TEST_FAILURE" in
    missing) echo 'Error response from daemon: No such image: sha256:previous-image' >&2; return 1 ;;
    other_missing) echo 'Error response from daemon: No such image: sha256:another-image' >&2; return 1 ;;
    daemon) echo 'Cannot connect to the Docker daemon' >&2; return 1 ;;
    timeout) return 124 ;;
  esac
}
'''
        return subprocess.run([BASH, '-c', harness + function + '\npreserve_code_image backend running-container attempt\nprintf "continued\\n"\n'],
                              env={**os.environ, 'PRESERVE_TEST_FAILURE': failure}, capture_output=True, text=True, timeout=10)

    def test_available_image_is_preserved(self):
        result = self.run_preservation()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('code_image_preserved', result.stdout)
        self.assertNotIn('code_image_unavailable', result.stdout)
        self.assertIn('continued', result.stdout)

    def test_missing_running_container_image_is_a_warning_not_a_blocker(self):
        result = self.run_preservation('missing')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertIn('code_image_unavailable', result.stdout)
        self.assertIn('rollback_image=unavailable existing_container=unchanged', result.stdout)
        self.assertNotIn('code_image_preserved', result.stdout)
        self.assertIn('continued', result.stdout)

    def test_other_errors_still_block_the_upgrade(self):
        for failure in ('other_missing', 'daemon', 'timeout'):
            with self.subTest(failure=failure):
                result = self.run_preservation(failure)
                self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
                self.assertIn('code_image_preserve_failed', result.stdout)
                self.assertNotIn('continued', result.stdout)

# Snapshot of the production 3d09add CLI, kept local so shallow CI clones can
# exercise migration without fetching historical commits or a Docker daemon.
LEGACY_READINESS = '''import json,sys
from urllib.request import urlopen
ROUTES=("/api/v1/catalog","/api/v1/economics/salary/catalog","/api/v1/economics/ipc/catalog",
        "/api/v1/economics/accounts/catalog","/api/v1/map/catalog","/api/v1/linear/catalog",
        "/api/v1/commissioning/annual/catalog","/api/v1/construction/catalog")
def probe(base="http://127.0.0.1:8000",expected=None):
    with urlopen(base+"/api/v1/health",timeout=20) as response: health=json.load(response)
    if expected and health.get("release")!=expected: raise RuntimeError("Backend still serves another release")
    for route in ROUTES:
        with urlopen(base+route,timeout=20) as response: payload=json.load(response)
        if route=="/api/v1/catalog" and not payload["controls"]["developers"]: raise RuntimeError("Backend has no developers")
if __name__=="__main__": probe(expected=sys.argv[1] if len(sys.argv)>1 else None)
'''


class LegacyIdentityProbeTests(unittest.TestCase):
    def test_host_probe_is_independent_of_the_actual_legacy_cli(self):
        with tempfile.TemporaryDirectory() as folder:
            root = Path(folder)
            old_cli = root / "readiness.py"
            old_cli.write_text(LEGACY_READINESS, encoding="utf-8")
            original = old_cli.read_bytes()
            health = {"status": "ok", "release": "/runtime/releases/old"}
            setup = ("import io,json,urllib.request\nhealth=" + repr(health) + "\n"
                     "urllib.request.urlopen=lambda url,**kwargs: io.StringIO(json.dumps(health if url.endswith('/health') else {'controls':{'developers':[{'developer':'OLD'}]}}))\n")
            old = subprocess.run([sys.executable, "-I", "-c", setup + "\n" + LEGACY_READINESS,
                                  "--identity-only", health["release"]], cwd=root, capture_output=True)
            self.assertNotEqual(old.returncode, 0)  # The old file treats the flag as the expected release.
            host = (REPO / "docker/update.sh").read_text(encoding="utf-8")
            code = re.search(r"python -c '(.*?)'\s+\"/runtime/\$target\"", host, re.S).group(1)
            for status, expected, success in (("ok", health["release"], True), ("ok", "/runtime/releases/other", False),
                                              ("unavailable", health["release"], False)):
                prefix = setup.replace("'status': 'ok'", "'status': '" + status + "'")
                result = subprocess.run([sys.executable, "-I", "-c", prefix + code, expected],
                                        cwd=root, capture_output=True)
                self.assertEqual(result.returncode == 0, success, result.stderr)
            self.assertEqual(old_cli.read_bytes(), original)

DAEMON = '''#!/usr/bin/env python3
import json,os,sys,runpy
from pathlib import Path
sys.path.insert(0, os.environ['TEST_REPO'])
from docker import publication as p
statefile=Path('daemon.json'); state=json.loads(statefile.read_text()); args=sys.argv[1:]
def save(): statefile.write_text(json.dumps(state))
def die(code): save(); sys.exit(code)
if args[0]=='inspect':
    if args[2]=='{{.Image}}': print('sha256:previous-image'); die(0)
    if '|{{.State.Status}}|' in args[2]: print(state['id']+'|'+state['status']+'|'+state['started'])
    else:
        details={'id':state['id'],'status':state['status'],'startedAt':state['started'],'exitCode':0,'oomKilled':False}
        if '.State.Health.Status' in args[2]: details['healthStatus']=state.get('health_status')
        if '"imageId":{{json .Image}}' in args[2]: details['imageId']='sha256:simulated-old-image'
        print(json.dumps(details))
    die(0)
if args[:2]==['image','tag']:
    state['image_tag_attempts']=state.get('image_tag_attempts',0)+1
    if state.get('missing_image'):
        print('Error response from daemon: No such image: '+args[2],file=sys.stderr); die(1)
    if state.get('image_tag_error'):
        print('Cannot connect to the Docker daemon',file=sys.stderr); die(1)
    state['image_tags']=state.get('image_tags',0)+1; die(0)
assert args[0]=='compose', args
args=args[1:]; command=args[0]
if args[:2]==['--profile','updater']: args=args[2:]; command=args[0]
if command=='build': state['builds']=state.get('builds',0)+1; die(0)
if command=='ps': print(state['id']); die(0)
if command=='run':
    assert '-T' in args and '--interactive=false' in args, 'Lifecycle must not attach an interactive terminal'
    assert os.read(0,1)==b'', 'Lifecycle must not consume operator stdin'
    if 'docker.publication' in args:
        action=args[-1]; runtime=Path('runtime')
        try:
            if action=='recovery-info':
                if state.get('hang_control'):
                    import signal,time
                    signal.signal(signal.SIGTERM,signal.SIG_IGN)
                    time.sleep(600)
                info=p.recovery_info(runtime); print(' '.join(info[k] or '-' for k in ('previous','next','live')))
            elif action=='archive': pass
            elif action=='publish':
                state['publications']=state.get('publications',0)+1
                target='releases/history-new'; (runtime/target).mkdir(exist_ok=True)
                (runtime/'pending.json').write_text(json.dumps({'previous':os.readlink(runtime/'live'),'next':target}))
                p.swap(runtime,target)
            else: p.transaction(runtime,action)
        except Exception as exc: print(str(exc),file=sys.stderr); die(1)
        die(0)
    state['downloads']+=1; die(0 if state.get('collected_success') else 1)
if command=='exec':
    assert '-T' in args and '--interactive=false' in args, 'Readiness must not attach an interactive terminal'
    assert os.read(0,1)==b'', 'Readiness must not consume operator stdin'
    if state.get('hang_probe'):
        import signal,time
        signal.signal(signal.SIGTERM,signal.SIG_IGN)
        time.sleep(600)
    if '-c' in args:
        # Execute the actual host-owned probe rather than guessing its flags.
        import io,urllib.request
        urllib.request.urlopen=lambda *a,**kw: io.StringIO(json.dumps({'status':state.get('health_http_status','ok' if state['id'] and state['status']=='running' else 'unavailable'),'release':'/runtime/'+state['served']}))
        code=args[args.index('-c')+1]; sys.argv=['-c',args[-1]]
        try: exec(compile(code,'host-identity-probe','exec'))
        except SystemExit: die(1)
        die(0)
    if state.get('old_cli'):
        assert '--identity-only' not in args, 'Old readiness.py has no flag support'
    die(0 if state['id'] and state['status']=='running' and args[-1]=='/runtime/'+state['served'] and state.get('full_ready',True) else 1)
if command=='stop': state['stops']+=1; state['status']='exited'; die(0)
if command in ('up','restart'):
    if args[-1]=='web': state['web_starts']=state.get('web_starts',0)+1; die(0)
    if command=='restart': state['restarts']+=1
    if not state['id'] or state['status']!='running' or command=='restart':
        state['starts']+=1; state['id']=state['id'] or 'backend-1'; state['status']='running'
        state['started']='start-'+str(state['starts']); state['served']=os.readlink('runtime/live')
    die(0)
raise AssertionError(args)
'''


class TransactionGuardTests(unittest.TestCase):
    def test_invalid_journal_cannot_mutate_even_with_a_valid_live_release(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / 'releases/new').mkdir(parents=True)
            pending = runtime / 'pending.json'
            for record in ({'previous': '', 'next': 'releases/missing'},
                           {'previous': None, 'next': 'releases/new'},
                           {'previous': 'releases/new', 'next': 'releases/new'},
                           {'previous': '', 'next': 'releases/new', 'unknown': True}):
                pending.write_text(json.dumps(record))
                with patch.object(p, 'live_target', return_value='releases/new'), patch.object(p, 'swap') as swap:
                    with self.assertRaises(RuntimeError): p.transaction(runtime, 'rollback')
                    swap.assert_not_called()
                self.assertEqual(json.loads(pending.read_text()), record)

    def test_bootstrap_can_be_acknowledged_only_after_pointer_activation(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            (runtime / 'releases/new').mkdir(parents=True)
            pending = runtime / 'pending.json'
            pending.write_text(json.dumps({'previous': '', 'next': 'releases/new'}))
            with patch.object(p, 'live_target', return_value=''):
                with self.assertRaises(RuntimeError): p.transaction(runtime, 'ack')
            self.assertTrue(pending.exists())
            with patch.object(p, 'live_target', return_value='releases/new'):
                p.transaction(runtime, 'ack')
            self.assertFalse(pending.exists())
            self.assertEqual(json.loads((runtime / 'previous-good.json').read_text()),
                             {'current': 'releases/new', 'previous': ''})

    def test_rollback_remains_retryable_until_ack(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            for name in ('old', 'new'): (runtime / 'releases' / name).mkdir(parents=True)
            pending = runtime / 'pending.json'
            pending.write_text(json.dumps({'previous': 'releases/old', 'next': 'releases/new'}))
            actual = ['releases/new']
            with patch.object(p, 'live_target', side_effect=lambda _runtime: actual[0]), \
                    patch.object(p, 'swap', side_effect=lambda _runtime, target: actual.__setitem__(0, target)):
                p.transaction(runtime, 'rollback')
                p.transaction(runtime, 'rollback')
                self.assertEqual(actual[0], 'releases/old')
                self.assertTrue(pending.exists())
                p.transaction(runtime, 'ack')
            self.assertFalse(pending.exists())

    def test_unrelated_live_and_empty_predecessor_cannot_mutate(self):
        with tempfile.TemporaryDirectory() as directory:
            runtime = Path(directory)
            for name in ('old', 'new', 'other'):
                (runtime / 'releases' / name).mkdir(parents=True)
            pending = runtime / 'pending.json'
            pending.write_text(json.dumps({'previous': 'releases/old', 'next': 'releases/new'}))
            with patch.object(p, 'live_target', return_value='releases/other'), patch.object(p, 'swap') as swap:
                for action in ('rollback', 'activate-next', 'ack'):
                    with self.assertRaises(RuntimeError): p.transaction(runtime, action)
                swap.assert_not_called()
            pending.write_text(json.dumps({'previous': '', 'next': 'releases/new'}))
            with patch.object(p, 'live_target', return_value='releases/new'), patch.object(p, 'swap') as swap:
                with self.assertRaises(RuntimeError): p.transaction(runtime, 'rollback')
                swap.assert_not_called()
            self.assertTrue(pending.exists())


@unittest.skipUnless(os.name != 'nt' and shutil.which('bash'), 'Host-shell recovery runs on Linux')
class HostRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'docker').mkdir()
        shutil.copy2(REPO / 'docker/update.sh', self.root / 'docker/update.sh')
        (self.root / '.env').write_text('SECRET=must-never-print\n')
        (self.root / 'bin').mkdir()
        executable = self.root / 'bin/docker'
        executable.write_text(DAEMON)
        executable.chmod(0o755)
        for name in ('old', 'new', 'other'):
            (self.root / 'runtime/releases' / name).mkdir(parents=True)
        (self.root / 'runtime/live').symlink_to('releases/new')
        self.state = dict(id='backend-1', status='running', started='original-start', served='releases/old',
                          starts=0, restarts=0, stops=0, downloads=0)

    def journal(self, previous='releases/old', next='releases/new'):
        (self.root / 'runtime/pending.json').write_text(json.dumps({'previous': previous, 'next': next}))

    def run_host(self, *args):
        (self.root / 'daemon.json').write_text(json.dumps(self.state))
        result = subprocess.run(['bash', 'docker/update.sh', *args], cwd=self.root,
            env={**os.environ, 'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
                 'TEST_REPO': str(REPO), 'READINESS_TIMEOUT': '2'}, input='operator terminal input\n', text=True, capture_output=True, timeout=35)
        self.state = json.loads((self.root / 'daemon.json').read_text())
        self.assertNotIn('must-never-print', result.stdout + result.stderr)
        self.assertTrue(list((self.root / 'logs').glob('docker-update_*.log')))
        return result

    def test_empty_previous_running_legacy_is_preserved_and_download_blocked(self):
        self.journal(previous='')
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.state['started'], self.state['stops'], self.state['restarts'], self.state['downloads']),
                         ('original-start', 0, 0, 0))
        self.assertTrue((self.root / 'runtime/pending.json').exists())
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/new')

    def test_healthy_next_after_crash_acknowledged_without_restart(self):
        self.journal(previous='')
        self.state['served'] = 'releases/new'
        result = self.run_host('--recover')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertFalse((self.root / 'runtime/pending.json').exists())
        self.assertEqual(self.state['started'], 'original-start')
        self.assertEqual(self.state['restarts'], 0)

    def test_healthy_previous_restores_pointer_without_restart(self):
        self.journal()
        result = self.run_host('--recover')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/old')
        self.assertFalse((self.root / 'runtime/pending.json').exists())
        self.assertEqual(self.state['started'], 'original-start')

    def test_unrelated_live_never_mutates_or_downloads(self):
        self.journal()
        (self.root / 'runtime/live').unlink()
        (self.root / 'runtime/live').symlink_to('releases/other')
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/other')
        self.assertEqual((self.state['stops'], self.state['starts'], self.state['downloads']), (0, 0, 0))

    def test_failed_source_without_pending_leaves_identity_and_start_time(self):
        self.state['served'] = 'releases/new'
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual((self.state['id'], self.state['started']), ('backend-1', 'original-start'))
        self.assertIn('backend ID and StartedAt unchanged', result.stdout)
        self.assertEqual((self.state['stops'], self.state['restarts']), (0, 0))
        self.assertEqual(self.state['downloads'], 1)

    def test_partial_pinned_previous_release_allows_collection_without_restart(self):
        self.state.update(served='releases/new', full_ready=False, old_cli=True)
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)  # Simulated source failure.
        self.assertEqual(self.state['downloads'], 1)
        self.assertEqual((self.state['started'], self.state['stops'], self.state['restarts']), ('original-start', 0, 0))

    def test_construction_history_failure_cannot_publish_or_start_new_web(self):
        self.state['served'] = 'releases/new'
        result = self.run_host('--construction-history')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state['downloads'], 1)
        self.assertEqual(self.state.get('publications', 0), 0)
        self.assertEqual(self.state.get('web_starts', 0), 0)
        self.assertEqual((self.state['started'], self.state['stops'], self.state['restarts']), ('original-start', 0, 0))
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/new')

    def test_construction_history_success_publishes_then_restarts_and_starts_web(self):
        self.state.update(served='releases/new', collected_success=True)
        result = self.run_host('--construction-history')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state['downloads'], 1)
        self.assertEqual(self.state['publications'], 1)
        self.assertEqual(self.state['web_starts'], 1)
        self.assertEqual((self.state['stops'], self.state['restarts']), (0, 1))
        self.assertEqual(self.state['served'], 'releases/history-new')
        self.assertFalse((self.root / 'runtime/pending.json').exists())

    def test_code_upgrade_proves_backend_before_web_without_source_collection(self):
        self.state['served'] = 'releases/new'
        result = self.run_host('--upgrade-code')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state['image_tags'], 2)
        self.assertEqual(self.state['builds'], 1)
        self.assertEqual(self.state['web_starts'], 1)
        self.assertEqual(self.state['downloads'], 0)
        self.assertEqual(self.state.get('publications', 0), 0)
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/new')
        self.assertIn('code_upgrade_complete', result.stdout)

    def test_code_upgrade_incompatible_backend_never_activates_web(self):
        self.state.update(served='releases/new', full_ready=False)
        result = self.run_host('--upgrade-code')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state['image_tags'], 2)
        self.assertEqual(self.state.get('web_starts', 0), 0)
        self.assertEqual(self.state['downloads'], 0)
        self.assertEqual(self.state.get('publications', 0), 0)
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/new')
        self.assertIn('code_upgrade_failed', result.stdout)

    def test_code_upgrade_missing_old_image_continues_without_a_false_backup_claim(self):
        self.state.update(served='releases/new', missing_image=True)
        result = self.run_host('--upgrade-code')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state['image_tag_attempts'], 2)
        self.assertEqual(self.state.get('image_tags', 0), 0)
        self.assertEqual((self.state['builds'], self.state['web_starts'], self.state['downloads'], self.state['stops']), (1, 1, 0, 0))
        self.assertIn('code_image_unavailable', result.stdout)
        self.assertNotIn('code_image_preserved', result.stdout)

    def test_code_upgrade_other_backup_failures_keep_the_existing_containers(self):
        self.state.update(served='releases/new', image_tag_error=True)
        result = self.run_host('--upgrade-code')
        self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(self.state.get('builds', 0), 0)
        self.assertEqual(self.state.get('web_starts', 0), 0)
        self.assertEqual((self.state['starts'], self.state['restarts'], self.state['stops']), (0, 0, 0))
        self.assertIn('code_image_preserve_failed', result.stdout)

    def test_new_modes_keep_missing_live_and_pending_recovery_guards(self):
        (self.root / 'runtime/live').unlink()
        for mode in ('--construction-history', '--upgrade-code'):
            with self.subTest(mode=mode, guard='missing-live'):
                result = self.run_host(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('reason=live_missing', result.stdout)
        (self.root / 'runtime/live').symlink_to('releases/new')
        (self.root / 'runtime/pending.json').write_text(json.dumps({'previous': None, 'next': 'releases/new'}))
        for mode in ('--construction-history', '--upgrade-code'):
            with self.subTest(mode=mode, guard='pending-recovery'):
                result = self.run_host(mode)
                self.assertNotEqual(result.returncode, 0)
                self.assertIn('recovery_blocked', result.stdout)
        self.assertEqual((self.state['starts'], self.state['stops'], self.state['downloads']), (0, 0, 0))
        self.assertEqual(self.state.get('builds', 0), 0)
        self.assertEqual(self.state.get('web_starts', 0), 0)

    def test_new_modes_share_the_host_lock_with_scheduled_updates(self):
        import fcntl
        (self.root / 'state').mkdir()
        (self.root / 'logs').mkdir()
        (self.root / 'logs/docker-update_previous.log').write_text('previous run')
        with (self.root / 'state/.docker-update.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            for mode in ('--construction-history', '--upgrade-code'):
                with self.subTest(mode=mode):
                    result = self.run_host(mode)
                    self.assertNotEqual(result.returncode, 0)
                    self.assertIn('Another Docker update is running', result.stderr)
        self.assertEqual((self.state['starts'], self.state['stops'], self.state['downloads']), (0, 0, 0))
        self.assertEqual(self.state.get('builds', 0), 0)

    def test_legacy_without_pending_cannot_start_mutable_downloads(self):
        self.state['served'] = '/app'
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('preflight_blocked', result.stdout)
        self.assertIn('reason=live_identity_not_ready', result.stdout)
        self.assertIn('step=readiness', result.stdout)
        self.assertEqual((self.state['stops'], self.state['restarts'], self.state['downloads']), (0, 0, 0))

    def test_missing_live_with_existing_backend_cannot_start_downloads(self):
        (self.root / 'runtime/live').unlink()
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('preflight_blocked', result.stdout)
        self.assertIn('reason=live_missing', result.stdout)
        self.assertNotIn('step=readiness', result.stdout)
        self.assertIn('--publish-existing', result.stdout)
        self.assertEqual((self.state['stops'], self.state['restarts'], self.state['downloads']), (0, 0, 0))

    def test_ordinary_live_directory_reports_migration_without_http_probe_or_mutation(self):
        (self.root / 'runtime/live').unlink()
        (self.root / 'runtime/live').mkdir()
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reason=live_not_symlink', result.stdout)
        self.assertIn('--publish-existing refuses an ordinary directory/file', result.stdout)
        self.assertNotIn('step=readiness', result.stdout)
        self.assertEqual((self.state['id'], self.state['started']), ('backend-1', 'original-start'))
        self.assertEqual((self.state['stops'], self.state['restarts'], self.state['downloads']), (0, 0, 0))
        self.assertTrue((self.root / 'runtime/live').is_dir())

    def test_failed_health_with_pinned_live_reports_health_and_blocks_collection(self):
        self.state.update(served='releases/new', health_status='unhealthy', health_http_status='unavailable')
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reason=live_identity_not_ready', result.stdout)
        self.assertIn('step=readiness', result.stdout)
        self.assertIn('"healthStatus": "unhealthy"', result.stdout)
        self.assertIn('"imageId": "sha256:simulated-old-image"', result.stdout)
        self.assertEqual((self.state['id'], self.state['started']), ('backend-1', 'original-start'))
        self.assertEqual((self.state['stops'], self.state['restarts'], self.state['downloads']), (0, 0, 0))

    def test_probe_ignoring_term_is_killed_without_changing_the_site(self):
        self.state.update(served='releases/new', hang_probe=True)
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reason=live_identity_not_ready', result.stdout)
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/new')
        self.assertEqual((self.state['started'], self.state['stops'], self.state['restarts'], self.state['downloads']),
                         ('original-start', 0, 0, 0))

    def test_control_timeout_does_not_claim_invalid_journal_or_mutate_it(self):
        import shlex
        self.journal(previous='')
        original = (self.root / 'runtime/pending.json').read_bytes()
        self.state.update(served='releases/new', hang_control=True)
        # Shorten only the control deadline; retain GNU timeout's real TERM/KILL
        # behaviour against a client that deliberately ignores TERM.
        real_timeout = shutil.which('timeout')
        wrapper = self.root / 'bin/timeout'
        wrapper.write_text('#!/usr/bin/env bash\n'
                           'if [[ "$2" == 60 ]]; then set -- "$1" 1 "${@:3}"; fi\n'
                           f'exec {shlex.quote(real_timeout)} "$@"\n')
        wrapper.chmod(0o755)
        result = self.run_host('--recover')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('reason=control_timeout', result.stdout)
        self.assertNotIn('Unknown journal/live combination', result.stdout)
        self.assertEqual((self.root / 'runtime/pending.json').read_bytes(), original)
        self.assertEqual(os.readlink(self.root / 'runtime/live'), 'releases/new')
        self.assertEqual((self.state['started'], self.state['stops'], self.state['restarts'], self.state['downloads']),
                         ('original-start', 0, 0, 0))

    def test_first_bootstrap_starts_only_once(self):
        self.journal(previous='')
        self.state.update(id='', status='absent', served='', started='')
        result = self.run_host('--recover')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.state['starts'], self.state['restarts'], self.state['downloads']), (1, 0, 0))
        self.assertFalse((self.root / 'runtime/pending.json').exists())


if __name__ == '__main__':
    unittest.main()
