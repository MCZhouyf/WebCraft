"""Restore saved decisions offline, then finish only missing remote A18 keys."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import importlib
import json
import resource
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

from scripts import run_a18_remote_cds as run
from scripts.a18_remote_backend import Coordinator, Endpoint, RemoteError, clear_author_config, load_author_config
from scripts.a18_saved_prefix import SavedPrefixClient, SavedPrefixEnd

BASE = run.OUT
OUT = BASE.parent / 'remote_service_resume1'
REPORT = run.REPORT.parent / 'remote_service_resume1'
NOTE = run.ROOT / 'prereg/a18_remote_recovery_20261018.md'


def context():
    sys.path.insert(0, str(run.WEB))
    sim = importlib.import_module('sim')
    manifest = {r['instance_id']: r for r in json.loads((run.WEB / 'cds_materials/cds_manifest.json').read_text())['rows']}
    tasks = {r['id']: r for r in run.read_jsonl(run.WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    conflicts = run.load_conflicts(run.WEB / 'data/conflicts.jsonl')
    nodes = run.load_resource_nodes(run.WEB / 'harness/config/arena.json')
    return sim, manifest, tasks, conflicts, nodes


def old_prompt(job):
    return BASE / 'prompts' / f"react__{job['condition']}__{job['instance_id']}.jsonl"


def missing_jobs():
    jobs = run.verify_inputs()
    records = run.read_jsonl(BASE / 'episodes.jsonl')
    complete = {(r['condition'], r['instance_id']) for r in records}
    if len(complete) != len(records) or len(records) != 182:
        raise RemoteError('original_checkpoint_not_182_unique_keys')
    if any(r['server_model_identifiers'] != ['coder390'] for r in records):
        raise RemoteError('original_checkpoint_model_mismatch')
    missing = [j for j in jobs if (j['condition'], j['instance_id']) not in complete]
    if len(missing) != 26:
        raise RemoteError('missing_scope_not_26')
    return missing


def verify_prefixes(jobs, ctx):
    results = []
    with tempfile.TemporaryDirectory(prefix='a18_offline_prefix_') as directory:
        coordinator = Coordinator(SimpleNamespace(model='coder390'), Path(directory))
        for job in jobs:
            source = old_prompt(job)
            if not source.exists():
                continue
            client = SavedPrefixClient(coordinator, source, offline_only=True)
            try:
                run.run_episode(job, coordinator, *ctx, client=client, out=Path(directory))
            except SavedPrefixEnd:
                if client.replayed != len(client.pairs):
                    raise RemoteError('offline_prefix_incomplete')
                results.append({'condition': job['condition'], 'instance_id': job['instance_id'],
                                'saved_replies': len(client.pairs), 'prompt_hashes_equal': True})
            else:
                raise RemoteError('interrupted_prefix_unexpectedly_complete')
    if len(results) != 4 or sum(r['saved_replies'] for r in results) != 62:
        raise RemoteError('offline_prefix_scope_changed')
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--offline-only', action='store_true')
    args = parser.parse_args()
    if run.effective_available_bytes() < int(1.8 * 1024**3):
        raise RemoteError('host_memory_gate')
    jobs, ctx = missing_jobs(), context()
    verified = verify_prefixes(jobs, ctx)
    if args.offline_only:
        print(json.dumps({'status': 'OFFLINE_PREFIX_PASS', 'new_model_calls': 0, 'prefixes': verified}))
        return 0
    if OUT.exists():
        raise RemoteError('recovery_output_already_exists')
    if subprocess.check_output(['git', 'status', '--porcelain', '--', str(NOTE)], cwd=run.ROOT, text=True).strip():
        raise RemoteError('recovery_note_not_committed')
    commit = subprocess.check_output(['git', 'log', '-1', '--format=%H', '--', str(NOTE)],
                                     cwd=run.ROOT, text=True).strip()
    if not commit:
        raise RemoteError('recovery_note_not_committed')
    start = time.monotonic()
    endpoint = fd = lock = coordinator = None
    complete = 0
    OUT.mkdir(parents=True)
    run.write(OUT / 'run_manifest.json', {'jobs': jobs, 'planned': 26, 'parent_completed': 182,
              'workers': 4, 'required_server_model_identifier': 'coder390', 'recovery_commit': commit,
              'parent_manifest_sha256': run.sha(BASE / 'run_manifest.json'),
              'parent_completed_sha256': run.sha(BASE / 'episodes.jsonl'),
              'request_timeout_seconds': 900, 'offline_prefix_verification': verified})
    failure = None
    try:
        lock = Path('/root/autodl-tmp/server.lock').open('r')
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        config, fd = load_author_config()
        endpoint = Endpoint(config, timeout=900)
        config.clear()
        if endpoint.model != 'coder390':
            raise RemoteError('recovery_model_changed')
        metadata = endpoint.request('models')
        if endpoint.model not in [r.get('id') for r in metadata.get('data', [])]:
            raise RemoteError('recovery_model_not_advertised')
        coordinator = Coordinator(endpoint, OUT)
        coordinator.canary()
        def execute(job):
            client = SavedPrefixClient(coordinator, old_prompt(job))
            return run.run_episode(job, coordinator, *ctx, client=client, out=OUT)
        iterator = iter(jobs)
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            pending = {}
            def enqueue():
                job = next(iterator, None)
                if job is not None and not coordinator.failed:
                    pending[pool.submit(execute, job)] = job
            for _ in range(4):
                enqueue()
            while pending:
                ready, _ = concurrent.futures.wait(pending, return_when=concurrent.futures.FIRST_COMPLETED)
                for future in ready:
                    job = pending.pop(future)
                    try:
                        row = future.result()
                    except Exception as exc:
                        coordinator.fail(exc.code if isinstance(exc, RemoteError) else type(exc).__name__)
                        coordinator.append(OUT / 'interrupted_episodes.jsonl', {
                            'condition': job['condition'], 'instance_id': job['instance_id'],
                            'error_type': type(exc).__name__, 'included_as_cognitive_failure': False})
                        continue
                    coordinator.append(OUT / 'episodes.jsonl', row)
                    complete += 1
                    progress = {'status': 'RUNNING', 'completed': complete, 'planned': 26,
                                'total_completed': 182 + complete, 'total_planned': 208,
                                'new_generations': coordinator.calls,
                                'elapsed_seconds': time.monotonic() - start}
                    run.write(OUT / 'progress.json', progress)
                    print(json.dumps(progress), flush=True)
                    enqueue()
        failure = coordinator.failed
    except Exception as exc:
        failure = exc.code if isinstance(exc, RemoteError) else type(exc).__name__
    finally:
        if endpoint is not None:
            endpoint.close()
        clear_author_config(fd)
        if lock is not None:
            fcntl.flock(lock, fcntl.LOCK_UN)
            lock.close()
    status = {'status': 'STOPPED_INFRASTRUCTURE' if failure else 'COMPLETED', 'safe_code': failure,
              'completed': complete, 'planned': 26, 'total_completed': 182 + complete,
              'calls': coordinator.calls if coordinator else 0, 'elapsed_seconds': time.monotonic() - start,
              'peak_RSS_KiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'owned_forward_closed': True, 'task_private_credentials_created': False}
    run.write(OUT / 'status.json', status)
    run.write(REPORT / 'status.json', status)
    print(json.dumps(status), flush=True)
    return int(bool(failure))


if __name__ == '__main__':
    raise SystemExit(main())
