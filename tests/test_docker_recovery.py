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
    if '|{{.State.Status}}|' in args[2]: print(state['id']+'|'+state['status']+'|'+state['started'])
    else: print(json.dumps({'id':state['id'],'status':state['status'],'startedAt':state['started'],'exitCode':0,'oomKilled':False}))
    die(0)
assert args[0]=='compose', args
args=args[1:]; command=args[0]
if command=='ps': print(state['id']); die(0)
if command=='run':
    if 'docker.publication' in args:
        action=args[-1]; runtime=Path('runtime')
        try:
            if action=='recovery-info':
                info=p.recovery_info(runtime); print(' '.join(info[k] or '-' for k in ('previous','next','live')))
            elif action=='archive': pass
            else: p.transaction(runtime,action)
        except Exception as exc: print(str(exc),file=sys.stderr); die(1)
        die(0)
    state['downloads']+=1; die(1)
if command=='exec':
    if '-c' in args:
        # Execute the actual host-owned probe rather than guessing its flags.
        import io,urllib.request
        urllib.request.urlopen=lambda *a,**kw: io.StringIO(json.dumps({'status':'ok' if state['id'] and state['status']=='running' else 'unavailable','release':'/runtime/'+state['served']}))
        code=args[args.index('-c')+1]; sys.argv=['-c',args[-1]]
        try: exec(compile(code,'host-identity-probe','exec'))
        except SystemExit: die(1)
        die(0)
    if state.get('old_cli'):
        assert '--identity-only' not in args, 'Old readiness.py has no flag support'
    die(0 if state['id'] and state['status']=='running' and args[-1]=='/runtime/'+state['served'] and state.get('full_ready',True) else 1)
if command=='stop': state['stops']+=1; state['status']='exited'; die(0)
if command in ('up','restart'):
    if args[-1]=='web': die(0)
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
                 'TEST_REPO': str(REPO), 'READINESS_TIMEOUT': '2'}, text=True, capture_output=True, timeout=35)
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

    def test_legacy_without_pending_cannot_start_mutable_downloads(self):
        self.state['served'] = '/app'
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('preflight_blocked', result.stdout)
        self.assertEqual((self.state['stops'], self.state['restarts'], self.state['downloads']), (0, 0, 0))

    def test_missing_live_with_existing_backend_cannot_start_downloads(self):
        (self.root / 'runtime/live').unlink()
        result = self.run_host()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('preflight_blocked', result.stdout)
        self.assertEqual((self.state['stops'], self.state['restarts'], self.state['downloads']), (0, 0, 0))

    def test_first_bootstrap_starts_only_once(self):
        self.journal(previous='')
        self.state.update(id='', status='absent', served='', started='')
        result = self.run_host('--recover')
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual((self.state['starts'], self.state['restarts'], self.state['downloads']), (1, 0, 0))
        self.assertFalse((self.root / 'runtime/pending.json').exists())


if __name__ == '__main__':
    unittest.main()
