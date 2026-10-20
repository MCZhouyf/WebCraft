"""A17 isolated workers around the unchanged external-agent loop."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import random
import resource
import sqlite3
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from arbiter.checkpoint import effective_available_bytes
from arbiter.evaluation.external_agents import ExternalEpisode, public_system_prompt, run_external_episode
from arbiter.evaluation.external_controls import public_task
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace
from arbiter.adapters import PublicSemanticAdapter
from arbiter.llm.gateway import GatewayError, GatewayProtocolError
from arbiter.llm.openai_probe import ProbeRefusal
from scripts.a17_gateway_preflight import PRIVATE, SECRET_ENV, configure
from scripts.run_cds_agents_20261017 import AuditedClient

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'artifacts/post_review/a17_ext_agents_20261017'
REPORT = ROOT / 'reports/main/post_review/a17_ext_agents_20261017'
DATABASE = OUT / 'task_queue.sqlite'
METHODS = ('direct_llm', 'react', 'reflexion')
DEADLINE = datetime(2026, 10, 20, tzinfo=ZoneInfo('America/Tijuana')).timestamp()
MIN_MEMORY = int(1.8 * 1024**3)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path):
    return [json.loads(s) for s in path.read_text().splitlines() if s.strip()]


def append(path, row):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open('a') as handle:
        handle.write(json.dumps(row, ensure_ascii=False, sort_keys=True) + '\n')


def connect():
    db = sqlite3.connect(DATABASE, timeout=30)
    db.row_factory = sqlite3.Row
    return db


def claimed_job(slot):
    with connect() as db:
        db.execute('BEGIN IMMEDIATE')
        row = db.execute("SELECT * FROM jobs WHERE status='pending' AND method NOT IN (SELECT method FROM blocked_methods) ORDER BY rank LIMIT 1").fetchone()
        if row is None:
            return None
        db.execute("UPDATE jobs SET status='running',slot=? WHERE job_id=?", (slot, row['job_id']))
        return dict(row)


def set_status(job_id, status, reason=''):
    with connect() as db:
        db.execute('UPDATE jobs SET status=?,reason=? WHERE job_id=?', (status, reason, job_id))


class MethodBlocked(GatewayError):
    pass


class DeadlineReached(GatewayError):
    pass


class Client(AuditedClient):
    current_method = None

    def chat(self, *args, **kwargs):
        if time.time() >= DEADLINE:
            raise DeadlineReached('registered_deadline')
        if self.current_method:
            with connect() as db:
                if db.execute('SELECT 1 FROM blocked_methods WHERE method=?', (self.current_method,)).fetchone():
                    raise MethodBlocked('method_identity_guard')
        try:
            return super().chat(*args, **kwargs)
        except GatewayProtocolError:
            if self.current_method:
                with connect() as db:
                    db.execute('INSERT OR IGNORE INTO blocked_methods VALUES (?)', (self.current_method,))
            raise


def prepare():
    if (OUT / 'status.json').exists():
        raise RuntimeError('registered_run_already_closed_no_reinitialization')
    OUT.mkdir(parents=True, exist_ok=True)
    REPORT.mkdir(parents=True, exist_ok=True)
    if DATABASE.exists():
        raise RuntimeError('task_queue_already_exists_no_reinitialization')
    prereg = ROOT / 'prereg/a17_ext_agents_20261017.md'
    if not prereg.is_file():
        raise RuntimeError('missing_preregistration')
    freeze = json.loads((WEB / 'config/ext_v2/freeze_manifest.json').read_text())
    mismatches = [name for name, expected in freeze['inputs'].items() if not (WEB / name).is_file() or sha(WEB / name) != expected]
    if mismatches:
        (REPORT / 'freeze_exception.json').write_text(json.dumps({'affected_inputs': mismatches, 'status': 'NOT_REPORTED_frozen_inputs_changed'}, indent=2) + '\n')
        raise RuntimeError('frozen_input_mismatch')
    config = json.loads((WEB / 'config/ext_v2/web_cells.json').read_text())
    tasks = {t['id']: t for t in jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    jobs = [{'job_id': f'{method}__{cell["condition"]}__{cell["world"]}__{task_id}',
             'method': method, 'condition': cell['condition'], 'world': cell['world'], 'task_id': task_id}
            for method in METHODS for cell in config['cells'] for task_id in config['web_task_ids']]
    if len(jobs) != 180 or len(config['web_task_ids']) != 12 or any(j['task_id'] not in tasks for j in jobs):
        raise RuntimeError('frozen_scope_mismatch')
    random.Random(20261017).shuffle(jobs)
    with connect() as db:
        db.execute('CREATE TABLE jobs (job_id TEXT PRIMARY KEY,method TEXT,condition TEXT,world TEXT,task_id TEXT,rank INTEGER,status TEXT,slot INTEGER,reason TEXT)')
        db.execute('CREATE TABLE blocked_methods (method TEXT PRIMARY KEY)')
        db.executemany('INSERT INTO jobs VALUES (?,?,?,?,?,?,?,NULL,?)',
                       [(j['job_id'], j['method'], j['condition'], j['world'], j['task_id'], rank, 'pending', '') for rank, j in enumerate(jobs)])
    source_hashes = {}
    for directory in ('sim', 'truth', 'config'):
        for p in sorted((WEB / directory).rglob('*')):
            if p.is_file() and '__pycache__' not in p.parts:
                source_hashes[str(p.relative_to(WEB))] = sha(p)
    for cell in config['cells']:
        for p in sorted((WEB / 'corpus/indexes/formal_p1' / cell['condition']).iterdir()):
            if p.is_file():
                source_hashes[str(p.relative_to(WEB))] = sha(p)
    for name in ('data/ext_v2/ext_v2_tasks_20261016.jsonl', 'data/conflicts.jsonl', 'config/ext_v2/web_cells.json'):
        source_hashes[name] = sha(WEB / name)
    manifest = {'prereg_commit': '77324cc', 'prereg_sha256': sha(prereg), 'seed': 20261017,
                'methods': list(METHODS), 'episodes': len(jobs), 'jobs': jobs,
                'requested_model': 'glm-5.2', 'max_actions': 90, 'max_calls': 40,
                'prompt_sha256': {m: hashlib.sha256(public_system_prompt(m).encode()).hexdigest() for m in METHODS},
                'external_runner_sha256': sha(ROOT / 'src/arbiter/evaluation/external_agents.py'),
                'deadline_local': '2026-10-20 00:00 America/Tijuana', 'webcraft_input_hashes': source_hashes,
                'webcraft_commit': subprocess.check_output(['git', '-C', str(WEB), 'rev-parse', 'HEAD'], text=True).strip(),
                'server_lock_sha256': sha(Path('/root/autodl-tmp/server.lock'))}
    (OUT / 'run_manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + '\n')
    forecast = {'estimated': True, 'currency': 'CNY', 'methods': [], 'provider_invoice': 'NOT_AVAILABLE',
                'prices_per_million_tokens': {'uncached_input': 8, 'cached_input': 2, 'output': 28}}
    historical = {'direct_llm': 'direct_llm_assembled', 'react': 'react_assembled', 'reflexion': 'reflexion_assembled'}
    for method, folder in historical.items():
        path = ROOT / 'artifacts/post_review/external_agents/final' / folder / 'episodes.jsonl'
        rows = jsonl(path)
        calls = sum(r['model_calls'] for r in rows) * 60 / len(rows)
        inputs = sum(r.get('input_tokens', 0) for r in rows) * 60 / len(rows)
        outputs = sum(r.get('output_tokens', 0) for r in rows) * 60 / len(rows)
        forecast['methods'].append({'method': method, 'episodes': 60, 'predicted_task_calls': calls,
                                    'cached_input_lower_estimate_CNY': (inputs * 2 + outputs * 28) / 1e6,
                                    'uncached_input_estimate_CNY': (inputs * 8 + outputs * 28) / 1e6,
                                    'historical_profile_sha256': sha(path)})
    for field in ('predicted_task_calls', 'cached_input_lower_estimate_CNY', 'uncached_input_estimate_CNY'):
        forecast[field] = sum(r[field] for r in forecast['methods'])
    (REPORT / 'cost_forecast.json').write_text(json.dumps(forecast, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'stage': 'prepared', 'episodes': len(jobs), 'forecast_task_calls': forecast['predicted_task_calls'],
                      'forecast_CNY_low': forecast['cached_input_lower_estimate_CNY'], 'forecast_CNY_uncached': forecast['uncached_input_estimate_CNY']}), flush=True)


def worker(slot):
    config = json.loads(PRIVATE.read_text())
    configure(config, slot)
    config.clear()
    worker_dir = OUT / f'worker_{slot}'
    worker_dir.mkdir(exist_ok=True)
    client = Client(worker_dir / 'ledger.jsonl', model='glm-5.2')
    client.min_interval_seconds = 2.0
    stats = {'slot': slot, 'status': 'completed', 'episodes_completed': 0, 'stop_reason': None}
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        client.validate_canary(thinking='disabled')
        sys.path.insert(0, str(WEB))
        sim = importlib.import_module('sim')
        tasks = {t['id']: t for t in jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
        conflicts = load_conflicts(WEB / 'data/conflicts.jsonl')
        nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
        manifest = json.loads((OUT / 'run_manifest.json').read_text())
        while time.time() < DEADLINE:
            if effective_available_bytes() < MIN_MEMORY:
                append(worker_dir / 'resource_waits.jsonl', {'wait_seconds': 600, 'available_bytes': effective_available_bytes()})
                time.sleep(min(600, max(0, DEADLINE - time.time())))
                continue
            job = claimed_job(slot)
            if job is None:
                break
            method, task = job['method'], tasks[job['task_id']]
            client.current_method = method
            if hashlib.sha256(public_system_prompt(method).encode()).hexdigest() != manifest['prompt_sha256'][method]:
                set_status(job['job_id'], 'not_reported', 'prompt_hash_changed')
                continue
            action_path = worker_dir / 'actions' / f'{job["job_id"]}.jsonl'
            client.audit_path = worker_dir / 'prompts' / f'{job["job_id"]}.jsonl'
            initial = Counter({r['name']: int(r['count']) for r in task['standard_items'] + task.get('task_supplements', [])})
            before = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
            handle = None
            episode = None
            refusal = False
            try:
                handle = sim.create_simulated_agent(version=job['world'], log_path=action_path,
                    initial_inventory=initial, search_condition=job['condition'],
                    search_index_dir=str(WEB / 'corpus/indexes/formal_p1' / job['condition']))
                api = PublicSemanticAdapter(handle['api'])
                try:
                    episode = run_external_episode(api, client, method=method, task=public_task(task),
                                                   max_calls=40, max_actions=90, thinking='disabled')
                except ProbeRefusal:
                    refusal = True
                    episode = ExternalEpisode(False, None, client.task_calls - before[0],
                        client.task_input_tokens - before[1], client.task_output_tokens - before[2], 0,
                        None, 'model_refusal', (), 0.0)
                if episode.model_output_error or refusal:
                    api.submit({'feasible': None}, intent='commit')
                final_inventory = api.inventory(intent='commit')
                handle['evaluator'].finish_episode()
            except GatewayError as exc:
                set_status(job['job_id'], 'infrastructure_missing', type(exc).__name__)
                append(worker_dir / 'aborted.jsonl', {k: job[k] for k in ('job_id', 'method', 'condition', 'world', 'task_id')} |
                       {'error_type': type(exc).__name__, 'included_in_metrics': False,
                        'action_log': str(action_path.relative_to(ROOT))})
                if isinstance(exc, MethodBlocked):
                    continue
                stats.update(status='partial', stop_reason=type(exc).__name__)
                break
            except Exception as exc:
                set_status(job['job_id'], 'not_reported', type(exc).__name__)
                append(worker_dir / 'exceptions.jsonl', {'job_id': job['job_id'], 'error_type': type(exc).__name__})
                continue
            finally:
                if handle is not None:
                    handle['shutdown']()
            if episode is None:
                continue
            events = jsonl(action_path)
            feasible = bool(task['feasible'][job['world']])
            quantity = sum(int(r['count']) for r in final_inventory if r['name'].removeprefix('minecraft:') == task['target']['item'])
            success = int((feasible and quantity >= int(task['target']['count']) and episode.reported_feasible is True)
                          or (not feasible and episode.reported_feasible is False))
            calls = client.task_calls - before[0]
            legacy = score_external_trace(events, world=job['world'], conflicts=conflicts,
                                         resource_nodes=nodes, true_feasible=feasible, reported_feasible=episode.reported_feasible)
            scored = outcome({'world': job['world'], 'true_feasible': feasible,
                              'reported_feasible': episode.reported_feasible, 'metrics': {'SR': success},
                              'model_calls': calls, 'diagnostics': list(episode.diagnostics), '_max_calls': 40, '_max_actions': 90}, events, task)
            record = {k: job[k] for k in ('job_id', 'method', 'condition', 'world', 'task_id')}
            record.update(schema_version=1, record_type='a17_ext_web_episode', reading_mode='all_public', seed=20261017,
                          family_id=task['independent_family_id'], true_feasible=feasible, reported_feasible=episode.reported_feasible,
                          success=success, model_calls=calls, input_tokens=client.task_input_tokens - before[1],
                          output_tokens=client.task_output_tokens - before[2], model_requested='glm-5.2',
                          server_model_identifiers=['glm-5.2'], metrics=legacy, gold_score=scored,
                          model_output_error=episode.model_output_error, diagnostics=list(episode.diagnostics),
                          action_log=str(action_path.relative_to(ROOT)), prompt_log=str(client.audit_path.relative_to(ROOT)),
                          prompt_sha256=manifest['prompt_sha256'][method], canonical_attribution_status='NOT_IDENTIFIABLE_no_operator_provenance')
            append(worker_dir / 'episodes.jsonl', record)
            set_status(job['job_id'], 'completed')
            stats['episodes_completed'] += 1
            print(json.dumps({'slot': slot, 'completed': stats['episodes_completed'], 'job_id': job['job_id'],
                              'calls': calls, 'success': success}), flush=True)
    except Exception as exc:
        stats.update(status='partial', stop_reason=type(exc).__name__)
    finally:
        stats.update(task_calls=client.task_calls, successful_provider_responses=client.calls,
                     input_tokens=client.input_tokens, output_tokens=client.output_tokens,
                     server_model_identifiers=sorted(client.returned_models), peak_rss_before_kib=rss_before,
                     peak_rss_after_kib=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
                     token_rate_estimated_CNY=client.priced_cost)
        for name in SECRET_ENV:
            os.environ.pop(name, None)
        (worker_dir / 'status.json').write_text(json.dumps(stats, sort_keys=True, indent=2) + '\n')
        print(json.dumps(stats, sort_keys=True), flush=True)


def run():
    if (OUT / 'status.json').exists():
        raise RuntimeError('registered_run_already_closed_no_rerun')
    if not DATABASE.exists():
        prepare()
    private_sha = sha(PRIVATE) if PRIVATE.is_file() else None
    children = []
    started = time.monotonic()
    cleanup = {'task_environment_cleared': False, 'consumed_private_config_removed': False,
               'private_runtime_files_created': 0, 'unrelated_credential_files_touched': False}
    try:
        if effective_available_bytes() < MIN_MEMORY:
            while effective_available_bytes() < MIN_MEMORY and time.time() < DEADLINE:
                time.sleep(min(600, max(0, DEADLINE - time.time())))
        for slot in range(1, 7):
            status_path = OUT / 'preflight' / f'slot_{slot}_status.json'
            if not status_path.is_file() or json.loads(status_path.read_text()).get('status') != 'passed':
                continue
            env = os.environ.copy()
            env.update(PYTHONDONTWRITEBYTECODE='1', PYTHONPATH=str(ROOT / 'src') + ':' + str(ROOT / 'scripts') + ':' + str(ROOT),
                       OPENBLAS_NUM_THREADS='1', OMP_NUM_THREADS='1', MKL_NUM_THREADS='1', CUDA_VISIBLE_DEVICES='')
            log = (OUT / f'worker_{slot}.log').open('w')
            process = subprocess.Popen(['nice', '-n', '15', sys.executable, '-m', 'scripts.run_a17_ext_agents', '--worker', str(slot)],
                                       cwd=ROOT, env=env, stdout=log, stderr=subprocess.STDOUT, text=True)
            children.append((process, log))
        for process, log in children:
            while process.poll() is None:
                if time.time() >= DEADLINE:
                    process.terminate()
                time.sleep(2)
            log.close()
    finally:
        for name in SECRET_ENV:
            os.environ.pop(name, None)
        cleanup['task_environment_cleared'] = True
        if private_sha and PRIVATE.is_file() and sha(PRIVATE) == private_sha:
            PRIVATE.unlink()
            cleanup['consumed_private_config_removed'] = True
        elif PRIVATE.exists():
            cleanup['exception'] = 'configuration_changed_concurrently_not_deleted'
        else:
            cleanup['consumed_private_config_removed'] = True
        REPORT.mkdir(parents=True, exist_ok=True)
        (REPORT / 'credential_cleanup.json').write_text(json.dumps(cleanup, indent=2, sort_keys=True) + '\n')
    with connect() as db:
        db.execute("UPDATE jobs SET status='not_reported',reason='method_identity_guard' WHERE status='pending' AND method IN (SELECT method FROM blocked_methods)")
        db.execute("UPDATE jobs SET status='not_reported',reason='no_available_worker_or_deadline' WHERE status IN ('pending','running')")
        counts = dict(db.execute('SELECT status,count(*) FROM jobs GROUP BY status').fetchall())
    summary = {'status': 'completed' if counts.get('completed') == 180 else 'partial', 'jobs': counts,
               'workers_started': len(children), 'elapsed_seconds': time.monotonic() - started,
               'prereg_commit': '77324cc', 'credential_cleanup_complete': cleanup['consumed_private_config_removed']}
    (OUT / 'status.json').write_text(json.dumps(summary, indent=2, sort_keys=True) + '\n')
    print(json.dumps(summary, sort_keys=True), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepare', action='store_true')
    parser.add_argument('--run', action='store_true')
    parser.add_argument('--worker', type=int, choices=range(1, 7))
    args = parser.parse_args()
    if args.worker:
        worker(args.worker)
    elif args.prepare:
        prepare()
    elif args.run:
        run()
    else:
        parser.error('choose --prepare, --run or --worker')


if __name__ == '__main__':
    main()
