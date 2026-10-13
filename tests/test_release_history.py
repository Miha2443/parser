"""Old release archiving must preserve live data and rollback reserves."""
import json
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import Mock, patch

from docker import publication as publication
from docker.release_history import archive_releases


class ReleaseHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.runtime = Path(self.temporary.name)
        self.names = [f"202610{day:02d}T060002-{'a' * 12}" for day in range(1, 6)]
        for name in self.names:
            release = self.runtime / "releases" / name
            release.mkdir(parents=True)
            (release / "data.xlsx").write_bytes(name.encode())
        self.live = "releases/" + self.names[-1]
        self.probe = Mock()

    def archive(self, **kwargs):
        # The production path still validates every directory; only symlink
        # lookup is mocked because Windows may not allow creating symlinks.
        with patch.object(publication, "live_target", return_value=self.live):
            return archive_releases(self.runtime, probe=self.probe, **kwargs)

    def test_only_old_releases_move_and_metadata_survives(self):
        original = self.runtime / "releases" / self.names[0] / "data.xlsx"
        timestamp = original.stat().st_mtime_ns
        moved = self.archive()
        self.assertEqual(set(moved), set(self.names[:2]))
        self.assertEqual({path.name for path in (self.runtime / "releases").iterdir()}, set(self.names[2:]))
        archived = self.runtime / "_archive/releases" / self.names[0] / "data.xlsx"
        self.assertEqual(archived.read_bytes(), self.names[0].encode())
        self.assertEqual(archived.stat().st_mtime_ns, timestamp)
        record = json.loads(archived.parent.with_suffix(".json").read_text())
        self.assertEqual(record["status"], "archived")
        self.assertEqual(record["original_path"], "releases/" + self.names[0])
        self.probe.assert_called_once_with(base="http://backend:8000", expected=str(self.runtime / self.live))
        self.assertEqual(self.archive(), [])

    def test_pending_restart_blocks_every_move(self):
        (self.runtime / "pending.json").write_text("{}")
        with self.assertRaisesRegex(RuntimeError, "recovery is pending"):
            self.archive()
        self.probe.assert_not_called()
        self.assertFalse((self.runtime / "_archive").exists())

    def test_backend_pinned_to_old_release_blocks_every_move(self):
        self.probe.side_effect = RuntimeError("Backend still serves another release")
        with self.assertRaisesRegex(RuntimeError, "another release"):
            self.archive()
        self.assertEqual(len(list((self.runtime / "releases").iterdir())), 5)

    def test_explicit_older_rollback_reserve_survives_newer_failed_releases(self):
        (self.runtime / "previous-good.json").write_text(json.dumps({
            "current": self.live, "previous": "releases/" + self.names[0],
        }))
        self.archive(keep=2)
        self.assertTrue((self.runtime / "releases" / self.names[0]).is_dir())
        self.assertTrue((self.runtime / self.live).is_dir())
        self.assertEqual(len(list((self.runtime / "releases").iterdir())), 2)

    def test_destination_collision_never_overwrites_history(self):
        existing = self.runtime / "_archive/releases" / self.names[0]
        existing.mkdir(parents=True)
        (existing / "operator.txt").write_text("keep me")
        with self.assertRaisesRegex(RuntimeError, "already contains"):
            self.archive()
        self.assertEqual((existing / "operator.txt").read_text(), "keep me")
        self.assertEqual(len(list((self.runtime / "releases").iterdir())), 5)

    def test_unknown_and_building_directories_are_untouched(self):
        for name in ("manual-backup", ".building-interrupted"):
            (self.runtime / "releases" / name).mkdir()
        self.archive()
        self.assertTrue((self.runtime / "releases/manual-backup").is_dir())
        self.assertTrue((self.runtime / "releases/.building-interrupted").is_dir())

    def test_symlink_release_is_rejected(self):
        directory = self.runtime / "releases" / self.names[0]
        (directory / "data.xlsx").unlink()
        directory.rmdir()
        outside = self.runtime / "operator"
        outside.mkdir()
        try:
            directory.symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest("Host does not allow directory symlinks")
        with self.assertRaisesRegex(RuntimeError, "Unsafe release"):
            self.archive()
        self.assertFalse((self.runtime / "_archive").exists())

    def test_keep_one_is_rejected_before_probe(self):
        with self.assertRaises(ValueError):
            self.archive(keep=1)
        self.probe.assert_not_called()

    def test_interrupted_move_keeps_files_and_retry_finishes_metadata(self):
        from docker import release_history
        write = release_history._write_record
        count = 0

        def fail_after_move(path, record):
            nonlocal count
            count += 1
            if count == 2:
                raise OSError("simulated metadata disk failure")
            write(path, record)

        with patch.object(release_history, "_write_record", side_effect=fail_after_move):
            with self.assertRaises(OSError):
                self.archive()
        name = self.names[1]
        archived = self.runtime / "_archive/releases" / name
        self.assertEqual((archived / "data.xlsx").read_bytes(), name.encode())
        metadata = archived.with_suffix(".json")
        self.assertEqual(json.loads(metadata.read_text())["status"], "prepared")
        self.archive()
        self.assertEqual(json.loads(metadata.read_text())["status"], "archived")
        self.assertTrue((self.runtime / self.live).is_dir())

    def test_ack_records_reserve_but_rollback_ack_does_not_replace_it(self):
        pending = self.runtime / "pending.json"
        record = {"previous": "releases/" + self.names[0], "next": self.live}
        pending.write_text(json.dumps(record))
        with patch.object(publication, "live_target", return_value=self.live):
            publication.transaction(self.runtime, "ack")
        reserve = json.loads((self.runtime / "previous-good.json").read_text())
        self.assertEqual(reserve, {"current": self.live, "previous": record["previous"]})
        pending.write_text(json.dumps({"previous": self.live, "next": "releases/" + self.names[3]}))
        with patch.object(publication, "live_target", return_value=self.live):
            publication.transaction(self.runtime, "ack")
        self.assertEqual(json.loads((self.runtime / "previous-good.json").read_text()), reserve)


if __name__ == "__main__":
    unittest.main()
