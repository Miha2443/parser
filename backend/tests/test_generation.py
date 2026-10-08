"""Publication, cache and failure invariants shared by multi-source reports."""
from pathlib import Path
import unittest
from unittest.mock import patch

from backend.generation_service import GenerationService
from backend.profile_service import SourceUnavailable
from pipeline.data_access import DataContext


class Access:
    version = 1
    fail = False
    change_on_load = False
    reads = 0

    def __init__(self, context):
        self.issues = []

    def data_version(self, name):
        return self.version

    def load_one(self):
        type(self).reads += 1
        if self.fail:
            self.issues.append(("error", "Source failed"))
        if self.change_on_load:
            type(self).version += 1
        return [self.version]

    def source_files(self, family):
        return []

    def latest_realty_mart_source_date(self, family):
        return {"one": "30.09.2026", "two": "01.10.2026"}[family]


class GenerationTests(unittest.TestCase):
    def setUp(self):
        Access.version, Access.reads, Access.fail, Access.change_on_load = 1, 0, False, False
        self.patcher = patch("backend.generation_service.DataAccess", Access)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
        self.service = GenerationService(DataContext(Path.cwd()), ("load_one",), ("one", "two"),
                                         lambda data: {"options": data["load_one"]},
                                         lambda data, catalog, **filters: {"rows": data["load_one"]}, 0)

    def test_copy_cache_refresh_and_date_order(self):
        before = self.service.catalog()
        self.assertEqual(before["source"]["date"], "01.10.2026")
        before["options"].clear()
        self.assertEqual(self.service.catalog()["options"], [1])
        self.assertEqual(Access.reads, 1)
        Access.version = 2
        after = self.service.catalog()
        self.assertNotEqual(before["version"], after["version"])
        self.assertEqual(self.service.report()["rows"], [2])

    def test_failed_refresh_never_serves_previous_generation(self):
        self.service.catalog()
        Access.version, Access.fail = 2, True
        for _ in range(2):
            with self.assertRaises(SourceUnavailable):
                self.service.report()
        Access.fail = False
        self.assertEqual(self.service.report()["rows"], [2])

    def test_changes_while_loading_never_publish(self):
        Access.change_on_load = True
        with self.assertRaises(SourceUnavailable):
            self.service.catalog()
        self.assertIsNone(self.service._catalog)


if __name__ == "__main__":
    unittest.main()
