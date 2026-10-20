import json
import random
import tempfile
import unittest
from pathlib import Path

from corpus.claims_cache import (CLAIM_KEYS, CONTRACT_VERSION, FORBIDDEN_KEYS,
                                 ClaimsCache, _has_forbidden_key)
from corpus.search_backend import SearchBackend
from sim.semantic_agent import _ApiFacade, create_simulated_agent


ROOT = Path(__file__).resolve().parents[2]
CACHE_ROOT = ROOT / 'corpus' / 'claims_cache'
PUBLIC_CLAIM_KEYS_V2 = {
    'claim_id', 'chunk_id', 'claim_type', 'subject_item', 'payload', 'polarity',
    'tense', 'span_text', 'extractor_version', 'parent_claim_id', 'normalizer_version',
}


def cache_specs():
    specs = []
    for path in sorted(CACHE_ROOT.glob('**/chunk_claims.jsonl')):
        relative = path.relative_to(CACHE_ROOT)
        if relative.parts[0] == 'formal_p1':
            index_root = ROOT / 'corpus' / 'indexes' / 'formal_p1' / relative.parts[1]
        else:
            index_root = ROOT / 'corpus' / 'indexes' / 'formal' / relative.parts[0]
        specs.append((path, index_root, index_root.name))
    return specs


@unittest.skipUnless(CACHE_ROOT.is_dir(), 'claims cache is unavailable')
class ClaimsCacheIsolationTests(unittest.TestCase):
    def test_claim_rows_match_the_public_contract(self):
        self.assertEqual(CONTRACT_VERSION, 'v2')
        self.assertEqual(CLAIM_KEYS, PUBLIC_CLAIM_KEYS_V2)
        for cache_path, index_dir, _ in cache_specs():
            with self.subTest(cache=cache_path.parent.name):
                cache = ClaimsCache(str(index_dir))
                for line in cache_path.read_text().splitlines():
                    row = json.loads(line)
                    self.assertEqual(set(row), {'chunk_id', 'claims'})
                    for claim in row['claims']:
                        self.assertTrue(set(claim) <= PUBLIC_CLAIM_KEYS_V2)
                        self.assertFalse(FORBIDDEN_KEYS & set(claim))
                        self.assertFalse(_has_forbidden_key(claim))
                    expected = []
                    for stored_claim in row['claims']:
                        claim = dict(stored_claim)
                        claim.setdefault('parent_claim_id', None)
                        claim.setdefault('normalizer_version', None)
                        expected.append(claim)
                    self.assertEqual(cache.get(row['chunk_id']), expected)

    def test_seeded_claim_spans_are_substrings_of_search_backend_chunks(self):
        candidates = []
        documents = {}
        for cache_path, index_dir, condition in cache_specs():
            backend = SearchBackend(condition, str(index_dir))
            documents[str(index_dir)] = {row['chunk_id']: row['text'] for row in backend.documents}
            for row in (json.loads(line) for line in cache_path.read_text().splitlines() if line):
                for claim in row['claims']:
                    candidates.append((str(index_dir), claim))
        self.assertGreaterEqual(len(candidates), 100)
        for index_dir, claim in random.Random(20261007).sample(candidates, 100):
            with self.subTest(claim=claim['claim_id']):
                self.assertIn(claim['span_text'], documents[index_dir][claim['chunk_id']])

    def test_simulator_facade_and_schema_v2_log_do_not_reference_cache(self):
        index_dir = ROOT / 'corpus' / 'indexes' / 'formal_p1' / 'mixed'
        with tempfile.TemporaryDirectory() as directory:
            log_path = Path(directory) / 'public.jsonl'
            handle = create_simulated_agent(
                version='1.21.11', log_path=log_path, search_condition='mixed',
                search_index_dir=str(index_dir),
            )
            try:
                api = handle['api']
                self.assertEqual(_ApiFacade.__slots__, ('_token',))
                for name in ('ClaimsCache', 'claims_cache', 'claim_cache', 'claims'):
                    self.assertFalse(hasattr(api, name), name)
                api.search('crafting', 5)
                payload = log_path.read_text()
                self.assertNotIn('ClaimsCache', payload)
                self.assertNotIn('claims_cache', payload)
                self.assertNotIn(str(CACHE_ROOT), payload)
                self.assertNotIn(str(index_dir), payload)
            finally:
                handle['shutdown']()
