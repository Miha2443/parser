"""Offline publication repairs derived files without downloading or mutating live data."""
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from unittest.mock import patch

from docker import publication as p


class OfflineRepairTests(unittest.TestCase):
    def test_real_ipc_etl_repairs_private_candidate_and_reuses_the_result(self):
        import pandas as pd
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, candidate, previous = (root / name for name in ("work", "candidate", "previous"))
            downloads = work / "downloads"
            downloads.mkdir(parents=True)
            processed = work / "data/processed"
            processed.mkdir(parents=True)
            target = processed / "ipc.pkl"
            pd.DataFrame([{"value": 1}]).to_pickle(target)
            os.utime(target, (1700000000, 1700000000))
            for part, year, value in ((1, 2020, 101.25), (2, 2026, 102.5)):
                source = downloads / f"Индексы потребительских цен_часть{part}_fixture.xlsx"
                rows = [[None] * 5 for _ in range(5)]
                rows[2][4], rows[3][4], rows[4][4] = year, "январь", "К предыдущему месяцу"
                rows += [[None, "процент", region, "Все товары и услуги", value]
                         for region in ("Москва", "Российская Федерация")]
                pd.DataFrame(rows).to_excel(source, sheet_name="Данные", header=False, index=False)
                os.utime(source, (1700000010, 1700000010))
            shutil.copytree(work, previous)
            shutil.copytree(work, candidate)
            originals = {file: (file.read_bytes(), file.stat().st_mtime_ns)
                         for base in (work, previous) for file in base.rglob("*") if file.is_file()}
            with patch("pipeline.source_availability.indicator_ids", return_value={"ipc"}):
                p.build_candidate_processed(candidate, {"fedstat"})
                with patch.object(p.subprocess, "run") as run:
                    p.build_candidate_processed(candidate, {"fedstat"})
                    run.assert_not_called()
            frame = pd.read_pickle(candidate / "data/processed/ipc.pkl")
            self.assertEqual(set(frame.value), {101.25, 102.5})
            self.assertEqual(set(frame.year), {2020, 2026})
            self.assertEqual(len(set(frame.source_file)), 2)
            self.assertTrue(all(file.read_bytes() == content and file.stat().st_mtime_ns == stamp
                                for file, (content, stamp) in originals.items()))

    def test_offline_publication_repairs_before_marts_and_validation(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, runtime = root / "work", root / "runtime"
            events = []
            with patch.object(p, "live_target", return_value=""), \
                    patch.object(p, "select_candidate_erz", return_value={"selection": "current", "reason": "test"}), \
                    patch.object(p, "publication_status", return_value={"optional_sources": {}}), \
                    patch.object(p, "build_candidate_processed", side_effect=lambda path, sources: events.append(("processed", path, sources))), \
                    patch.object(p, "build_candidate", side_effect=lambda path: events.append(("marts", path))), \
                    patch.object(p, "validate", side_effect=lambda path: events.append(("validate", path))), \
                    patch.object(p, "swap") as swap:
                p.publish(work, runtime)
            self.assertEqual([event[0] for event in events], ["processed", "marts", "validate"])
            self.assertEqual(events[0][2], {"fedstat", "rosstat"})
            self.assertTrue(all(event[1].parent == runtime / "releases" for event in events))
            self.assertNotEqual(events[0][1], work)
            swap.assert_called_once()

    def test_failed_offline_repair_cannot_publish_or_change_existing_release(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            work, runtime = root / "work", root / "runtime"
            previous = runtime / "releases/old"
            previous.mkdir(parents=True)
            marker = previous / "untouched"
            marker.write_bytes(b"previous release")
            with patch.object(p, "live_target", return_value="releases/old"), \
                    patch.object(p, "select_candidate_erz"), \
                    patch.object(p, "publication_status"), \
                    patch.object(p, "build_candidate_processed", side_effect=subprocess.CalledProcessError(1, ["local ETL"])), \
                    patch.object(p, "build_candidate") as marts, patch.object(p, "swap") as swap:
                with self.assertRaises(subprocess.CalledProcessError):
                    p.publish(work, runtime)
            marts.assert_not_called()
            swap.assert_not_called()
            self.assertFalse((runtime / "pending.json").exists())
            self.assertEqual(marker.read_bytes(), b"previous release")
            self.assertEqual(list((runtime / "releases").iterdir()), [previous])


if __name__ == "__main__":
    unittest.main()
