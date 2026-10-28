from pathlib import Path
import unittest
from unittest.mock import Mock

from backend.annual_routes import LOADERS, catalog, create_service, report
from backend.generation_service import GenerationService
from pipeline.annual_calculations import ANNUAL_CALCULATION_REVISION
from pipeline.data_access import DataContext


class CalculationRevisionTests(unittest.TestCase):
    def test_annual_semantic_change_invalidates_old_versions_without_changing_other_services(self):
        context = DataContext(Path.cwd())
        access = Mock()
        access.data_version.side_effect = lambda name: name + '-unchanged-source'
        legacy = GenerationService(context, LOADERS, (), catalog, report)
        annual = create_service(context)
        original = context.cache_key(), tuple((name, name + '-unchanged-source') for name in LOADERS)
        self.assertEqual(legacy._versions(access), original)
        self.assertEqual(annual._versions(access), (ANNUAL_CALCULATION_REVISION, original))
        self.assertNotEqual(annual._versions(access), legacy._versions(access))


if __name__ == '__main__':
    unittest.main()
