"""Run a separate, bounded-concurrency A18 segment on an existing service."""
from __future__ import annotations

import argparse
import concurrent.futures
import fcntl
import hashlib
import importlib
import json
import random
import resource
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path

from arbiter.adapters import PublicSemanticAdapter
from arbiter.checkpoint import effective_available_bytes
from arbiter.evaluation.cds_agents import (
    CDSKnowledgeAdapter, boundary_metrics, boundary_truth, public_rows,
    replace_search_results, verify_w7a_freeze,
)
from arbiter.evaluation.external_agents import public_system_prompt, run_external_episode
from arbiter.evaluation.external_controls import public_task
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace
from scripts.a18_remote_backend import (
    Coordinator, Endpoint, RemoteError, WorkerClient, clear_author_config,
    load_author_config, load_private_config,
)
from scripts.run_cds_agents_20261017 import boundary_call, goal_count

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'artifacts/post_review/a18_qwen_cds_20261017/remote_service'
REPORT = ROOT / 'reports/main/post_review/a18_qwen_cds_20261017/remote_service'
AMENDMENT = ROOT / 'prereg/a18_remote_service_amendment_20261017.md'


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def verify_inputs():
    verify_w7a_freeze(WEB)
    lock = json.loads((ROOT / 'prereg/a18_cds_inputs_20261017.json').read_text())
    for relative, expected in lock['source_hashes'].items():
        if sha(ROOT / relative) != expected:
            raise RemoteError('frozen_source_changed')
    if hashlib.sha256(public_system_prompt('react').encode()).hexdigest() != lock['react_prompt_sha256']:
        raise RemoteError('frozen_react_prompt_changed')
    if sha(WEB / 'cds_materials/cds_manifest.json') != lock['manifest_sha256']:
        raise RemoteError('material_manifest_changed')
    jobs = [dict(job) for job in lock['jobs']]
    if len(jobs) != 208 or len({(j['condition'], j['instance_id']) for j in jobs}) != 208:
        raise RemoteError('registered_scope_changed')
    for job in jobs:
        if sha(WEB / job['visible_path']) != job['visible_sha256']:
            raise RemoteError('visible_material_changed')
    random.Random(20261017).shuffle(jobs)
    return jobs


def run_episode(job, coordinator, sim, manifest, tasks, conflicts, nodes, *, client=None, out=None):
    client = client or WorkerClient(coordinator)
    out = OUT if out is None else out
    meta = manifest[job['instance_id']]
    material = json.loads((WEB / meta['visible_path']).read_text())
    evaluator = json.loads((WEB / meta['evaluator_path']).read_text())
    task = tasks[material['task_id']]
    initial = Counter({r['name']: int(r['count']) for r in task['standard_items'] + task.get('task_supplements', [])})
    stem = f"react__{job['condition']}__{job['instance_id']}"
    action_path = out / 'actions' / f'{stem}.jsonl'
    client.audit_path = out / 'prompts' / f'{stem}.jsonl'
    handle = sim.create_simulated_agent(
        version=material['target_world'], log_path=action_path, initial_inventory=initial,
        search_condition=f"matched_{material['target_world']}",
        search_index_dir=str(WEB / 'corpus/indexes/formal_p1' / f"matched_{material['target_world']}"),
    )
    api = CDSKnowledgeAdapter(PublicSemanticAdapter(handle['api']), public_rows(material, job['condition']))
    try:
        episode = run_external_episode(api, client, method='react', task=public_task(task),
                                       max_calls=40, max_actions=90, thinking='disabled')
        action_after = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
        if episode.model_output_error:
            api.submit({'feasible': None}, intent='commit')
        final_inventory = api.inventory(intent='commit')
        handle['evaluator'].finish_episode()
        boundary_response, predictions = boundary_call(client, task=task, trace=api.trace,
                                                       candidates=material['boundary_question'])
    finally:
        handle['shutdown']()
    if boundary_response['model'] != coordinator.model or coordinator.failed:
        raise RemoteError('global_circuit_open')
    replace_search_results(action_path, api.observations)
    events = read_jsonl(action_path)
    feasible = bool(task['feasible'][material['target_world']])
    attained = goal_count(final_inventory, task['target']) >= int(task['target']['count'])
    success = int((feasible and attained and episode.reported_feasible is True)
                  or (not feasible and episode.reported_feasible is False))
    legacy = score_external_trace(events, world=material['target_world'], conflicts=conflicts,
                                 resource_nodes=nodes, true_feasible=feasible,
                                 reported_feasible=episode.reported_feasible)
    scored = outcome({'world': material['target_world'], 'true_feasible': feasible,
                      'reported_feasible': episode.reported_feasible, 'metrics': {'SR': success},
                      'model_calls': action_after[0], 'diagnostics': list(episode.diagnostics),
                      '_max_calls': 40, '_max_actions': 90}, events, task)
    truth = boundary_truth(material, evaluator)
    return {'record_type': 'a18_remote_CDS_episode', 'method': 'react',
            'condition': job['condition'], 'instance_id': job['instance_id'], 'task_id': task['id'],
            'family_id': task['independent_family_id'], 'target_world': material['target_world'],
            'comparison_world': material['comparison_world'], 'seed': 20261017,
            'model_requested': coordinator.model, 'server_model_identifiers': [coordinator.model],
            'local_model_identifiers': [], 'weight_identity_verified': False,
            'true_feasible': feasible, 'reported_feasible': episode.reported_feasible,
            'success': success, 'terminal_attained': attained,
            'action_model_calls': action_after[0], 'boundary_model_calls': client.task_calls - action_after[0],
            'model_calls': client.task_calls, 'input_tokens': client.task_input_tokens,
            'replayed_saved_responses': getattr(client, 'replayed', 0),
            'output_tokens': client.task_output_tokens, 'tool_actions': episode.tool_actions,
            'primary_cost': episode.primary_cost, 'model_output_error': episode.model_output_error,
            'metrics': legacy, 'gold_score': scored, 'boundary_predictions': predictions,
            'boundary_truth': truth,
            'boundary': boundary_metrics(predictions, truth, material['boundary_question']),
            'action_log': str(action_path.relative_to(ROOT)), 'prompt_log': str(client.audit_path.relative_to(ROOT)),
            'visible_material_sha256': meta['visible_sha256'], 'estimated_provider_cost': None}


def main():
    parser = argparse.ArgumentParser()
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument('--private-config', type=Path)
    source.add_argument('--author-context', action='store_true')
    parser.add_argument('--workers', type=int, choices=range(1, 7), default=2)
    parser.add_argument('--preflight-only', action='store_true')
    args = parser.parse_args()
    endpoint = coordinator = lock_handle = author_fd = None
    created_output = False
    completed = 0
    started = time.monotonic()
    try:
        # Missing configuration never creates a misleading running artifact.
        if args.author_context:
            config, author_fd = load_author_config()
        else:
            config = load_private_config(args.private_config)
        if effective_available_bytes() < int(1.8 * 1024**3):
            raise RemoteError('host_memory_gate')
        if OUT.exists() and any(OUT.iterdir()):
            raise RemoteError('remote_output_must_be_new_no_silent_episode_replay')
        if subprocess.check_output(['git', 'status', '--porcelain', '--', str(AMENDMENT)],
                                   cwd=ROOT, text=True).strip():
            raise RemoteError('remote_amendment_not_committed')
        commit = subprocess.check_output(['git', 'log', '-1', '--format=%H', '--', str(AMENDMENT)],
                                         cwd=ROOT, text=True).strip()
        if not commit:
            raise RemoteError('remote_amendment_not_committed')
        jobs = verify_inputs()
        lock_handle = Path('/root/autodl-tmp/server.lock').open('r')
        fcntl.flock(lock_handle, fcntl.LOCK_SH | fcntl.LOCK_NB)
        OUT.mkdir(parents=True)
        created_output = True
        REPORT.mkdir(parents=True, exist_ok=True)
        # Persist identity and immutable scope before the first network request.
        write(OUT / 'run_manifest.json', {'amendment_commit': commit, 'amendment_sha256': sha(AMENDMENT),
              'jobs': jobs, 'planned': 208, 'workers': args.workers,
              'required_server_model_identifier': config['model'], 'weight_identity_verified': False,
              'started_at_utc': datetime.now(timezone.utc).isoformat(), 'preflight_only': args.preflight_only,
              'input_lock_sha256': sha(ROOT / 'prereg/a18_cds_inputs_20261017.json')})
        endpoint = Endpoint(config)
        config.clear()
        metadata = endpoint.request('models')
        ids = [r.get('id') for r in metadata.get('data', [])]
        if endpoint.model not in ids:
            raise RemoteError('configured_model_not_advertised')
        coordinator = Coordinator(endpoint, OUT)
        coordinator.canary()
        write(OUT / 'preflight.json', {'status': 'PASS', 'required_model': endpoint.model,
              'advertised_model_present': True, 'canary_passed': True, 'selected_workers': args.workers,
              'GPU_topology_verified': False})
        if not args.preflight_only:
            sys.path.insert(0, str(WEB))
            sim = importlib.import_module('sim')
            manifest = {r['instance_id']: r for r in json.loads((WEB / 'cds_materials/cds_manifest.json').read_text())['rows']}
            tasks = {r['id']: r for r in read_jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
            conflicts = load_conflicts(WEB / 'data/conflicts.jsonl')
            nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
            iterator = iter(jobs)
            with concurrent.futures.ThreadPoolExecutor(max_workers=args.workers) as pool:
                pending = {}
                def enqueue():
                    job = next(iterator, None)
                    if job is not None and not coordinator.failed:
                        future = pool.submit(run_episode, job, coordinator, sim, manifest, tasks, conflicts, nodes)
                        pending[future] = job
                for _ in range(args.workers):
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
                                'instance_id': job['instance_id'], 'condition': job['condition'],
                                'error_type': type(exc).__name__, 'included_as_cognitive_failure': False})
                            continue
                        coordinator.append(OUT / 'episodes.jsonl', row)
                        completed += 1
                        progress = {'status': 'RUNNING', 'completed': completed, 'planned': 208,
                                    'workers': args.workers, 'calls': coordinator.calls,
                                    'elapsed_seconds': time.monotonic() - started}
                        write(OUT / 'progress.json', progress)
                        print(json.dumps(progress), flush=True)
                        enqueue()
            if coordinator.failed:
                raise RemoteError(coordinator.failed)
        status = 'PREFLIGHT_ONLY' if args.preflight_only else 'COMPLETED'
        code = None
    except Exception as exc:
        status = 'STOPPED_INFRASTRUCTURE' if OUT.exists() else 'BLOCKED_CONFIGURATION_OR_PREFLIGHT'
        code = exc.code if isinstance(exc, RemoteError) else type(exc).__name__
    finally:
        if endpoint is not None:
            endpoint.close()
        clear_author_config(author_fd)
        if lock_handle is not None:
            fcntl.flock(lock_handle, fcntl.LOCK_UN)
            lock_handle.close()
    final = {'status': status, 'safe_code': code, 'completed': completed, 'planned': 208,
             'workers': args.workers, 'elapsed_seconds': time.monotonic() - started,
             'peak_RSS_KiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
             'calls': coordinator.calls if coordinator else 0, 'task_private_credentials_created': False,
             'owned_forward_closed': True, 'no_remote_service_or_GPU_process_mutation': True}
    if created_output:
        write(OUT / 'status.json', final)
        write(REPORT / 'status.json', final)
    print(json.dumps(final), flush=True)
    return int(code is not None)


if __name__ == '__main__':
    raise SystemExit(main())
