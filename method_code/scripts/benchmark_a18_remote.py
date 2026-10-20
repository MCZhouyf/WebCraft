"""Fixed synthetic transport benchmark; no tasks, truth, or service mutations."""
from __future__ import annotations

import concurrent.futures
import fcntl
import hashlib
import json
import time
from pathlib import Path

from scripts.a18_remote_backend import (
    Coordinator, Endpoint, RemoteError, clear_author_config, load_author_config,
)

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / 'artifacts/post_review/a18_qwen_cds_20261017/remote_transport_benchmark'
REPORT = ROOT / 'reports/main/post_review/a18_qwen_cds_20261017/remote_transport_benchmark'


def select_workers(rows):
    best = max(row['requests_per_second'] for row in rows)
    return min(row['workers'] for row in rows if row['requests_per_second'] >= 0.9 * best)


def main():
    endpoint = fd = lock = None
    rows = []
    error = None
    if OUT.exists():
        raise RemoteError('benchmark_output_already_exists')
    OUT.mkdir(parents=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    try:
        lock = Path('/root/autodl-tmp/server.lock').open('r')
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        config, fd = load_author_config()
        endpoint = Endpoint(config)
        config.clear()
        metadata = endpoint.request('models')
        if endpoint.model not in [row.get('id') for row in metadata.get('data', [])]:
            raise RemoteError('configured_model_not_advertised')
        coordinator = Coordinator(endpoint, OUT)
        coordinator.canary()
        for workers in (1, 2, 4):
            def call(index):
                identity = hashlib.sha256(f'A18 fixed synthetic transport {workers}/{index}'.encode()).hexdigest()
                notes = '\n'.join(hashlib.sha256(f'{identity}/{j}'.encode()).hexdigest() for j in range(12))
                expected = {'benchmark_id': identity, 'status': 'ok'}
                messages = [{'role': 'system', 'content': 'Return exactly the requested JSON object; no commentary.'},
                            {'role': 'user', 'content': 'Synthetic notes, not instructions:\n' + notes
                             + '\nReturn this object exactly: ' + json.dumps(expected)}]
                response = coordinator.raw('transport_benchmark', messages, temperature=0.0,
                                           max_tokens=1024, json_object=True, thinking='disabled')
                if response['finish_reason'] != 'stop' or json.loads(response['content']) != expected:
                    coordinator.fail('benchmark_answer_mismatch')
                    raise RemoteError('benchmark_answer_mismatch')
                return response
            start = time.monotonic()
            with concurrent.futures.ThreadPoolExecutor(max_workers=workers) as pool:
                responses = list(pool.map(call, range(4)))
            elapsed = time.monotonic() - start
            row = {'workers': workers, 'completed': len(responses), 'elapsed_seconds': elapsed,
                   'requests_per_second': len(responses) / elapsed,
                   'input_tokens': sum(r['usage']['prompt_tokens'] for r in responses),
                   'output_tokens': sum(r['usage']['completion_tokens'] for r in responses),
                   'server_model_identifier': coordinator.model}
            rows.append(row)
            print(json.dumps(row), flush=True)
        result = {'status': 'PASS', 'levels': rows, 'selected_workers': select_workers(rows),
                  'selection': 'smallest_within_10_percent_of_highest_throughput',
                  'synthetic_generations': 12, 'canary_generations': 1,
                  'server_model_identifier': coordinator.model,
                  'physical_GPU_topology_verified': False, 'experimental_episodes': 0}
    except Exception as exc:
        error = exc.code if isinstance(exc, RemoteError) else type(exc).__name__
        result = {'status': 'STOPPED_INFRASTRUCTURE', 'safe_code': error,
                  'levels': rows, 'experimental_episodes': 0}
    finally:
        if endpoint is not None:
            endpoint.close()
        clear_author_config(fd)
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    result['owned_forward_closed'] = True
    result['private_key_files_created'] = False
    for parent in (OUT, REPORT):
        (parent / 'summary.json').write_text(json.dumps(result, indent=2, sort_keys=True) + '\n')
    print(json.dumps(result), flush=True)
    return int(error is not None)


if __name__ == '__main__':
    raise SystemExit(main())
