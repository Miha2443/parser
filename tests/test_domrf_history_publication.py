"""Candidate validation must not publish an incomplete or damaged history bundle."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from docker.coverage import validate_construction_sources
from pipeline.data_access import DataAccess, DataContext
from pipeline.downloaders.nashdom_operational_history import download_history
from scripts.check_nashdom_operational_history import FakeSource


class HistoryPublicationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.raw = self.root / "data/raw/realty/nashdom"
        result = download_history(self.raw, client=FakeSource(), progress=lambda event: None)
        self.assertTrue(result["complete"])
        self.bundle = self.raw / "construction_history.json"
        self.access = DataAccess(DataContext(self.root, use_marts=False))
        self.current = self.access.load_construction_operational()

    def validate(self):
        validate_construction_sources(self.access, self.current, require_permits=False)

    def edit(self, change):
        value = json.loads(self.bundle.read_text(encoding="utf-8"))
        change(value)
        self.bundle.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")

    def test_complete_bootstrap_is_publishable(self):
        self.validate()

    def test_partial_or_failed_current_publication_is_rejected(self):
        self.edit(lambda value: value.update(complete=False))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.validate()
        self.edit(lambda value: value.update(complete=True, current_publication_error="price unavailable"))
        with self.assertRaisesRegex(RuntimeError, "incomplete"):
            self.validate()

    def test_changed_measurement_with_old_checksum_is_rejected(self):
        self.edit(lambda value: value["construction"][0].update(area_thousand_m2=123))
        with self.assertRaisesRegex(RuntimeError, "checksum"):
            self.validate()

    def test_claimed_coverage_cannot_replace_actual_observations(self):
        self.edit(lambda value: value["coverage"]["construction"].update(expected_months=4, collected_months=4))
        with self.assertRaisesRegex(RuntimeError, "periods"):
            self.validate()


if __name__ == "__main__":
    unittest.main()
