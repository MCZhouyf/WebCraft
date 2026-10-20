"""Reconstruct interrupted A21 requests offline before live continuation."""
from __future__ import annotations

import fcntl
import argparse
import json
import os
import tempfile
from pathlib import Path

from scripts import run_a21_mds as run
from scripts.a21_saved_prefix import select_prefix


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('--stage', choices=run.STAGES, default='development')
    args = parser.parse_args()
    if run.effective_available_bytes() < run.MIN_BYTES:
        raise RuntimeError('host_memory_gate')
    base = run.stage_output(args.stage)
    jobs = run.material_rows(args.stage)
    complete = {(r['condition'], r['instance_id']) for r in run.completed_records(base, jobs)}
    results = []
    lock = Path('/root/autodl-tmp/server.lock').open()
    fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
    old_env = os.environ.copy()
    run.clean_env()
    os.environ.update({'WC_LLM_API_KEY': 'offline-only-no-network', 'WC_LLM_MODEL': run.MODEL,
                       'WC_LLM_BASE_URL': 'https://example.invalid/v1', 'ARB_LLM_BUDGET': '1'})
    try:
        with tempfile.TemporaryDirectory(prefix='a21_offline_prefix_') as directory:
            for job in jobs:
                if (job['condition'], job['instance_id']) in complete:
                    continue
                stem = f"react__{job['condition']}__{job['instance_id']}"
                source, pairs, _ = select_prefix(base, stem, Path(directory) / 'unused')
                if not pairs:
                    continue
                client = run.PooledAuditedClient(Path(directory) / 'ledger.jsonl', model=run.MODEL)
                client.offline_only = True
                output = Path(directory) / job['instance_id']
                try:
                    run.run_one(job, stage=args.stage, worker=1, output=output, client=client)
                except run.SavedPrefixEnd:
                    if client.replayed != len(pairs):
                        raise RuntimeError('offline_prefix_not_fully_verified')
                else:
                    raise RuntimeError('interrupted_prefix_unexpectedly_terminal')
                results.append({'instance_id': job['instance_id'], 'saved_replies': len(pairs),
                                'source_path': str(source.relative_to(run.ROOT)),
                                'source_sha256': run.sha(source), 'all_prompts_and_observations_equal': True,
                                'pending_request_equal': True, 'new_model_calls': 0})
        report = {'status': 'OFFLINE_PREFIX_PASS', 'new_model_calls': 0, 'prefixes': results,
                  'saved_replies': sum(row['saved_replies'] for row in results)}
        stamp = run.datetime.now(run.timezone.utc).strftime('%Y%m%dT%H%M%S%fZ')
        run.write(run.REPORT / f'saved_prefix_verification_{args.stage}_{stamp}.json', report)
        print(json.dumps(report, sort_keys=True))
        return 0
    finally:
        os.environ.clear(); os.environ.update(old_env)
        run.clean_env()
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


if __name__ == '__main__':
    raise SystemExit(main())
