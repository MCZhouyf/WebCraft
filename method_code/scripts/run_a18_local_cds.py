"""Run the frozen 208 A18 jobs with one owned local model, no API/server."""
from __future__ import annotations

import argparse
import fcntl
import hashlib
import importlib
import json
import os
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
from scripts.a18_local_backend import DEADLINE, IDENTITY, LocalClient, append
from scripts.prepare_a18_local_model import LOCK as MODEL_LOCK, SNAPSHOT, VENDOR, sha
from scripts.run_cds_agents_20261017 import boundary_call, goal_count

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'artifacts/post_review/a18_qwen_cds_20261017/local_nf4'
REPORT = ROOT / 'reports/main/post_review/a18_qwen_cds_20261017/local_nf4'
INPUT_LOCK = ROOT / 'prereg/a18_cds_inputs_20261017.json'
AMENDMENT = ROOT / 'prereg/a18_local_gpu_amendment_20261017.md'


def jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(row, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    temporary.replace(path)


def prereg_commit():
    if subprocess.check_output(['git', 'status', '--porcelain', '--',
                                str(AMENDMENT.relative_to(ROOT)), str(MODEL_LOCK.relative_to(ROOT))],
                               cwd=ROOT, text=True).strip():
        raise RuntimeError('local_amendment_and_model_lock_must_be_committed')
    result = subprocess.check_output(['git', 'log', '-1', '--format=%H', '--',
                                      str(AMENDMENT.relative_to(ROOT))], cwd=ROOT, text=True).strip()
    if not result:
        raise RuntimeError('missing_committed_local_amendment')
    return result


def verify_inputs():
    lock = json.loads(INPUT_LOCK.read_text())
    verify_w7a_freeze(WEB)
    if hashlib.sha256(public_system_prompt('react').encode()).hexdigest() != lock['react_prompt_sha256']:
        raise RuntimeError('frozen_react_prompt_changed')
    for name, expected in lock['source_hashes'].items():
        if sha(ROOT / name) != expected:
            raise RuntimeError('frozen_A8_source_changed')
    model = json.loads(MODEL_LOCK.read_text())
    if sha(INPUT_LOCK) != model['a18_input_lock_sha256']:
        raise RuntimeError('A18_input_lock_changed')
    for name, value in model['weights'].items():
        # Preparation streamed each shard; repeat the full check before loading.
        if sha(SNAPSHOT / name) != value['sha256']:
            raise RuntimeError('local_weight_hash_changed')
    for name, expected in model['metadata'].items():
        if sha(SNAPSHOT / name) != expected:
            raise RuntimeError('local_model_metadata_changed')
    for name, expected in model['vendor_files'].items():
        if sha(VENDOR / name) != expected:
            raise RuntimeError('local_format_library_changed')
    jobs = [dict(row) for row in lock['jobs']]
    if len(jobs) != 208 or len({(j['condition'], j['instance_id']) for j in jobs}) != 208:
        raise RuntimeError('A18_registered_scope_changed')
    random.Random(20261017).shuffle(jobs)
    return jobs, model


def run_episode(job, client, sim, manifest, tasks, conflicts, nodes):
    meta = manifest[job['instance_id']]
    material = json.loads((WEB / meta['visible_path']).read_text())
    if sha(WEB / meta['visible_path']) != job['visible_sha256']:
        raise RuntimeError('visible_material_changed')
    evaluator = json.loads((WEB / meta['evaluator_path']).read_text())
    task = tasks[material['task_id']]
    initial = Counter({r['name']: int(r['count']) for r in task['standard_items'] + task.get('task_supplements', [])})
    stem = f"react__{job['condition']}__{job['instance_id']}"
    action_path, prompt_path = OUT / 'actions' / f'{stem}.jsonl', OUT / 'prompts' / f'{stem}.jsonl'
    client.audit_path = prompt_path
    handle = sim.create_simulated_agent(
        version=material['target_world'], log_path=action_path, initial_inventory=initial,
        search_condition=f"matched_{material['target_world']}",
        search_index_dir=str(WEB / 'corpus/indexes/formal_p1' / f"matched_{material['target_world']}"),
    )
    api = CDSKnowledgeAdapter(PublicSemanticAdapter(handle['api']), public_rows(material, job['condition']))
    before = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
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
    replace_search_results(action_path, api.observations)
    events = jsonl(action_path)
    feasible = bool(task['feasible'][material['target_world']])
    attained = goal_count(final_inventory, task['target']) >= int(task['target']['count'])
    success = int((feasible and attained and episode.reported_feasible is True)
                  or (not feasible and episode.reported_feasible is False))
    legacy = score_external_trace(events, world=material['target_world'], conflicts=conflicts,
                                 resource_nodes=nodes, true_feasible=feasible,
                                 reported_feasible=episode.reported_feasible)
    scored = outcome({'world': material['target_world'], 'true_feasible': feasible,
                      'reported_feasible': episode.reported_feasible, 'metrics': {'SR': success},
                      'model_calls': action_after[0] - before[0], 'diagnostics': list(episode.diagnostics),
                      '_max_calls': 40, '_max_actions': 90}, events, task)
    truth = boundary_truth(material, evaluator)
    after = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
    if boundary_response['model'] != IDENTITY or client.returned_models != {IDENTITY}:
        raise RuntimeError('local_identity_mismatch')
    return {'record_type': 'a18_local_CDS_episode', 'method': 'react',
            'condition': job['condition'], 'instance_id': job['instance_id'], 'task_id': task['id'],
            'family_id': task['independent_family_id'], 'target_world': material['target_world'],
            'comparison_world': material['comparison_world'], 'seed': 20261017,
            'model_requested': 'Qwen/Qwen3-14B', 'local_model_identifiers': [IDENTITY],
            'server_model_identifiers': [], 'identity_source': 'local_weight_lock_not_server',
            'true_feasible': feasible, 'reported_feasible': episode.reported_feasible,
            'success': success, 'terminal_attained': attained,
            'action_model_calls': action_after[0] - before[0],
            'boundary_model_calls': after[0] - action_after[0], 'model_calls': after[0] - before[0],
            'input_tokens': after[1] - before[1], 'output_tokens': after[2] - before[2],
            'tool_actions': episode.tool_actions, 'primary_cost': episode.primary_cost,
            'model_output_error': episode.model_output_error, 'metrics': legacy, 'gold_score': scored,
            'boundary_predictions': predictions, 'boundary_truth': truth,
            'boundary': boundary_metrics(predictions, truth, material['boundary_question']),
            'action_log': str(action_path.relative_to(ROOT)), 'prompt_log': str(prompt_path.relative_to(ROOT)),
            'visible_material_sha256': meta['visible_sha256'], 'estimated_provider_cost': None}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--preflight-only', action='store_true')
    parsed = parser.parse_args()
    if OUT.exists() and any(OUT.iterdir()):
        raise RuntimeError('local_run_directory_must_be_new_no_episode_replay')
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise RuntimeError('host_memory_gate_failed')
    commit = prereg_commit()
    OUT.mkdir(parents=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    start, client, completed, stopped, cleanup = time.monotonic(), None, 0, None, {}
    lock_path = Path('/root/autodl-tmp/arbiter_runtime/a18_gpu0.lock')
    lock_path.parent.mkdir(exist_ok=True)
    owned_lock = lock_path.open('a+')
    fcntl.flock(owned_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    jobs = []
    try:
        jobs, model = verify_inputs()
        write(OUT / 'run_manifest.json', {'jobs': jobs, 'episodes': 208, 'model_lock_sha256': sha(MODEL_LOCK),
              'amendment_commit': commit, 'amendment_sha256': sha(AMENDMENT),
              'started_at_utc': datetime.now(timezone.utc).isoformat(),
              'local_model_identifier': IDENTITY, 'server_model_identifier': None,
              'quantization': model['quantization'], 'API_calls': 0, 'inference_concurrency': 1,
              'preflight_only': parsed.preflight_only})
        client = LocalClient(OUT)
        client.validate_canary()
        write(OUT / 'preflight.json', {'status': 'PASS', 'local_model_identifier': IDENTITY,
              'gpu': client.admission, 'load_seconds': client.load_seconds,
              'cuda_allocated_bytes': client.torch.cuda.memory_allocated(0),
              'canary_responses': client.calls})
        if not parsed.preflight_only:
            sys.path.insert(0, str(WEB))
            sim = importlib.import_module('sim')
            manifest = {r['instance_id']: r for r in json.loads((WEB / 'cds_materials/cds_manifest.json').read_text())['rows']}
            tasks = {r['id']: r for r in jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
            conflicts = load_conflicts(WEB / 'data/conflicts.jsonl')
            nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
            for job in jobs:
                if time.time() >= DEADLINE:
                    raise RuntimeError('registered_hard_deadline')
                write(OUT / 'active_episode.json', {'condition': job['condition'], 'instance_id': job['instance_id']})
                row = run_episode(job, client, sim, manifest, tasks, conflicts, nodes)
                append(OUT / 'episodes.jsonl', row)
                completed += 1
                status = {'status': 'RUNNING', 'completed': completed, 'planned': 208,
                          'local_generations': client.calls, 'task_calls': client.task_calls,
                          'elapsed_seconds': time.monotonic() - start, 'API_calls': 0}
                write(OUT / 'progress.json', status)
                print(json.dumps(status), flush=True)
    except Exception as exc:
        stopped = type(exc).__name__
        append(OUT / 'infrastructure_abort.jsonl', {'error_type': stopped,
               'safe_code': str(exc)[:160] if type(exc).__module__ in {'builtins', 'arbiter.llm.gateway'} else None,
               'completed_checkpoint': completed, 'included_as_cognitive_failure': False})
    finally:
        if client is not None:
            cleanup = client.close()
        fcntl.flock(owned_lock, fcntl.LOCK_UN)
        owned_lock.close()
        lock_path.unlink(missing_ok=True)
        for name in ('WC_LLM_API_KEY', 'WC_LLM_API_KEYS', 'WC_LLM_BASE_URL', 'CUDA_VISIBLE_DEVICES'):
            os.environ.pop(name, None)
        status = {'status': 'STOPPED_INFRASTRUCTURE' if stopped else 'PREFLIGHT_ONLY' if parsed.preflight_only else 'COMPLETED',
                  'stop_reason': stopped, 'completed': completed, 'planned': 208,
                  'elapsed_seconds': time.monotonic() - start,
                  'peak_RSS_KiB': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                  'local_generations': client.calls if client else 0, 'API_calls': 0,
                  'cleanup': cleanup, 'task_gpu_lock_released': True,
                  'task_private_credentials_created': False}
        write(OUT / 'status.json', status)
        write(REPORT / 'status.json', status)
        print(json.dumps(status), flush=True)
    return 2 if stopped else 0


if __name__ == '__main__':
    main()
