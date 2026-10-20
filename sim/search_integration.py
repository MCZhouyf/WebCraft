"""Read-only integration validation between the simulator facade and formal BM25 indexes."""
from __future__ import annotations

import hashlib
import json
import random
import statistics
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from corpus.search_backend import RETURN_KEYS, tokenize
from sim.semantic_agent import PUBLIC_METHODS, _ApiFacade, create_simulated_agent

ROOT = Path(__file__).resolve().parents[1]
FORMAL_ROOT = ROOT / 'corpus' / 'indexes' / 'formal'
FORMAL_INDEXES = (
    'matched_1.16.5', 'matched_1.19', 'matched_1.21.11',
    'mismatched_old2new', 'mismatched_new2old', 'mixed',
)
FORBIDDEN_RESULT_MARKERS = ('truth_vector', 'synthetic', 'provisional')
FORBIDDEN_FACADE_ATTRIBUTES = ('search_condition', 'search_index_dir', 'backend', 'search_backend', 'SearchBackend')


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def index_info(name: str) -> dict[str, Any]:
    path = FORMAL_ROOT / name
    config = json.loads((path / 'config.json').read_text())
    if config.get('provisional') is not False:
        raise AssertionError(f'{name}: formal index must set provisional=false')
    if config.get('condition_name') != name or name not in config.get('accepted_conditions', []):
        raise AssertionError(f'{name}: condition/config mismatch')
    documents = [json.loads(line) for line in (path / 'documents.jsonl').read_text().splitlines() if line]
    if not documents:
        raise AssertionError(f'{name}: empty formal index')
    vocabulary = sorted({term for document in documents for term in tokenize(document['text']) if len(term) > 2})
    if not vocabulary:
        raise AssertionError(f'{name}: no searchable terms')
    return {'name': name, 'path': path, 'config': config, 'documents': documents, 'vocabulary': vocabulary}


def assert_facade_isolation(api: object) -> None:
    if sorted(name for name in dir(api) if not name.startswith('_')) != sorted(PUBLIC_METHODS):
        raise AssertionError('public method allowlist changed')
    if _ApiFacade.__slots__ != ('_token',):
        raise AssertionError('facade private storage changed')
    if any(hasattr(api, name) for name in FORBIDDEN_FACADE_ATTRIBUTES):
        raise AssertionError('facade exposes search configuration or backend')


def assert_result_contract(rows: list[dict]) -> None:
    if not rows:
        raise AssertionError('search returned no result for selected index vocabulary')
    for row in rows:
        if set(row) != RETURN_KEYS:
            raise AssertionError(f'search result keys differ: {sorted(row)}')
        lowered = row['text'].casefold()
        for marker in FORBIDDEN_RESULT_MARKERS:
            if marker in lowered:
                raise AssertionError(f'search result leaks {marker}')


def run_smoke(name: str, *, log_path: Path | None = None) -> dict[str, Any]:
    info = index_info(name)
    query = info['vocabulary'][0]
    temporary = None
    if log_path is None:
        temporary = tempfile.TemporaryDirectory()
        log_path = Path(temporary.name) / 'search.jsonl'
    handle = create_simulated_agent(
        version='1.21.11', log_path=log_path, search_condition=name,
        search_index_dir=str(info['path']),
    )
    try:
        api = handle['api']
        assert_facade_isolation(api)
        rows = api.search(query, 5)
        assert_result_contract(rows)
        events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
        if len(events) != 1:
            raise AssertionError(f'{name}: expected one logged search, got {len(events)}')
        event = events[0]
        if event['schema_version'] != 2 or event['method'] != 'search':
            raise AssertionError(f'{name}: invalid schema-v2 search event')
        if event['cost']['primary'] != 0:
            raise AssertionError(f'{name}: search primary cost is not zero')
        serialized = json.dumps(event, sort_keys=True)
        if str(info['path']) in serialized or name in serialized:
            raise AssertionError(f'{name}: logged private index configuration')
        if set(event['result'][0]) != RETURN_KEYS:
            raise AssertionError(f'{name}: log result contract mismatch')
        return {
            'index': name, 'condition': name, 'query': query, 'result_count': len(rows),
            'config_sha256': sha256(info['path'] / 'config.json'),
            'documents_sha256': sha256(info['path'] / 'documents.jsonl'),
            'document_count': len(info['documents']), 'page_count': info['config']['pages'],
        }
    finally:
        handle['shutdown']()
        if temporary is not None:
            temporary.cleanup()


def latency_samples(name: str, *, queries: int = 100) -> dict[str, Any]:
    info = index_info(name)
    rng = random.Random(20261007 + FORMAL_INDEXES.index(name))
    with tempfile.TemporaryDirectory() as directory:
        handle = create_simulated_agent(
            version='1.21.11', log_path=Path(directory) / 'latency.jsonl',
            search_condition=name, search_index_dir=str(info['path']),
        )
        try:
            samples = []
            for _ in range(queries):
                query = rng.choice(info['vocabulary'])
                started = time.perf_counter_ns()
                rows = handle['api'].search(query, 5)
                samples.append((time.perf_counter_ns() - started) / 1_000_000)
                assert_result_contract(rows)
        finally:
            handle['shutdown']()
    samples.sort()
    return {
        'query_count': queries, 'mean_latency_ms': round(statistics.fmean(samples), 3),
        'median_latency_ms': round(statistics.median(samples), 3),
        'p95_latency_ms': round(samples[round((queries - 1) * .95)], 3),
        'max_latency_ms': round(max(samples), 3),
    }


def main() -> None:
    if not FORMAL_ROOT.is_dir():
        raise SystemExit('STOP: formal corpus indexes are unavailable')
    rows = []
    for name in FORMAL_INDEXES:
        row = run_smoke(name)
        row.update(latency_samples(name))
        rows.append(row)
    report = {
        'timestamp': datetime.now(timezone.utc).isoformat(), 'status': 'PASS',
        'formal_index_count': len(rows), 'indexes': rows,
        'search_relevant_legacy_probe_checks': [
            'public search method remains in the exact eight-method allowlist',
            'facade exposes no search condition, index directory, or SearchBackend reference',
            'schema-v2 search log omits private index configuration and retains zero primary cost',
        ],
        'additional_search_result_checks': [
            'result text excludes truth_vector', 'result text excludes synthetic',
            'result text excludes provisional',
        ],
        'corpus_files_modified': False,
    }
    output = ROOT / 'reports' / 'sim_search_integration.json'
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'status': report['status'], 'formal_indexes': len(rows)}))


if __name__ == '__main__':
    main()
