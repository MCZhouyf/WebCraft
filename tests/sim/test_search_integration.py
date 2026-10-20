import json
import tempfile
import unittest
from pathlib import Path

from corpus.search_backend import RETURN_KEYS
from sim.search_integration import FORMAL_INDEXES, FORMAL_ROOT, run_smoke
from sim.semantic_agent import _ApiFacade, create_simulated_agent


@unittest.skipUnless(FORMAL_ROOT.is_dir(), 'formal corpus indexes are unavailable')
class SearchIntegrationTests(unittest.TestCase):
    def test_all_formal_indexes_use_public_zero_cost_search(self):
        for name in FORMAL_INDEXES:
            with self.subTest(index=name), tempfile.TemporaryDirectory() as directory:
                path = Path(directory) / 'search.jsonl'
                summary = run_smoke(name, log_path=path)
                self.assertGreater(summary['result_count'], 0)
                record = json.loads(path.read_text().splitlines()[0])
                self.assertEqual(record['schema_version'], 2)
                self.assertEqual(record['method'], 'search')
                self.assertEqual(record['cost']['primary'], 0)
                self.assertEqual(set(record['result'][0]), RETURN_KEYS)
                serialized = path.read_text().casefold()
                for marker in ('truth_vector', 'synthetic', 'provisional', 'indexes/formal'):
                    self.assertNotIn(marker, serialized)

    def test_facade_has_no_search_configuration_or_backend_reference(self):
        handle = create_simulated_agent(version='1.21.11')
        try:
            api = handle['api']
            self.assertEqual(_ApiFacade.__slots__, ('_token',))
            for name in ('search_condition', 'search_index_dir', 'backend', 'search_backend', 'SearchBackend'):
                self.assertFalse(hasattr(api, name), name)
        finally:
            handle['shutdown']()
