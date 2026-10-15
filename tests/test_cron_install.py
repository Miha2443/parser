"""Execute the real installer against an isolated crontab, without root or cron."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

REPO = Path(__file__).resolve().parents[1]


@unittest.skipUnless(os.name != 'nt' and shutil.which('bash'), 'Host cron installation runs on Linux')
class CronInstallTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'project with spaces'
        (self.root / 'docker').mkdir(parents=True)
        shutil.copy2(REPO / 'docker/install-cron.sh', self.root / 'docker/install-cron.sh')
        (self.root / 'bin').mkdir()
        crontab = self.root / 'bin/crontab'
        crontab.write_text('''#!/usr/bin/env python3
import os,sys
from pathlib import Path
path=Path(os.environ['FAKE_CRONTAB'])
if sys.argv[1]=='-l':
    if os.environ.get('CRONTAB_READ_ERROR'):
        print('crontab: permission denied',file=sys.stderr); sys.exit(1)
    if not path.exists():
        print('no crontab for op',file=sys.stderr); sys.exit(1)
    print(path.read_text(),end='')
else: path.write_bytes(Path(sys.argv[1]).read_bytes())
''')
        crontab.chmod(0o755)
        # A timezone/service mutation is forbidden, even when sudo is available.
        for name in ('timedatectl', 'sudo', 'systemctl', 'service'):
            executable = self.root / 'bin' / name
            executable.write_text('#!/bin/sh\necho forbidden-host-mutation >&2\nexit 99\n')
            executable.chmod(0o755)
        self.table = self.root / 'crontab.txt'

    def install(self, *args, fail_read=False):
        return subprocess.run(['bash', 'docker/install-cron.sh', *args], cwd=self.root,
            env={**os.environ, 'PATH': str(self.root / 'bin') + os.pathsep + os.environ['PATH'],
                 'FAKE_CRONTAB': str(self.table), 'CRONTAB_READ_ERROR': '1' if fail_read else ''},
            capture_output=True, text=True, timeout=10)

    def test_installs_daily_in_host_time_and_keeps_other_jobs(self):
        prior = 'MAILTO=operator@example.test\n15 2 * * * /opt/other/backup.sh\n'
        self.table.write_text(prior + f'0 6 * * * /bin/bash {self.root}/docker/update.sh\n')
        result = self.install()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        installed = self.table.read_text()
        self.assertTrue(installed.startswith(prior))
        self.assertEqual(installed.count('docker/update.sh'), 1)
        self.assertIn('0 6 * * * UPDATE_ERZ=1 /bin/bash ', installed)
        self.assertNotIn('CRON_TZ', installed)
        self.assertNotIn('forbidden-host-mutation', result.stderr)
        backups = list((self.root / 'state/cron-backups').glob('crontab-before-*.txt'))
        self.assertEqual(len(backups), 1)
        self.assertIn('/opt/other/backup.sh', backups[0].read_text())
        self.assertEqual(backups[0].stat().st_mode & 0o777, 0o600)
        second = self.install()
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(self.table.read_text(), installed)

    def test_fresh_crontab_and_custom_time(self):
        result = self.install('07:05')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn('5 7 * * * UPDATE_ERZ=1 ', self.table.read_text())

    def test_bad_time_or_unreadable_table_cannot_replace_jobs(self):
        original = '0 1 * * * /opt/backup.sh\n'
        self.table.write_text(original)
        for args, fail_read in ((('24:00',), False), ((), True)):
            result = self.install(*args, fail_read=fail_read)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(self.table.read_text(), original)


if __name__ == '__main__':
    unittest.main()
