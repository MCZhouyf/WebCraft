"""A22 development-only runner: cached shared-reader inputs, public simulator API."""
from __future__ import annotations

import argparse
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, as_completed
from dataclasses import asdict
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import resource
import sys
import time

from arbiter.adapters.webcraft import PublicSemanticAdapter
from arbiter.agent import ArbiterAgent, NAIVE_EXECUTION
from arbiter.agent.aggregation import naive_rag
from arbiter.agent.pf_rag import FilterState, PFRAGAgent
from arbiter.checkpoint import effective_available_bytes
from arbiter.contracts import Goal, ItemStack
from arbiter.evaluation.p6_execution import _claims, _compiler, _execution_policy
from arbiter.evaluation.p6_matrix import development_tasks
from preflight_a22_pf_rag import run as preflight


ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
BASE = ROOT / 'artifacts/post_review/a22_pf_rag/development'
REPORT = ROOT / 'reports/main/post_review/a22_pf_rag/development'
DEADLINE = datetime(2026, 10, 20, 14, tzinfo=timezone.utc)
SEMANTIC = {'mine', 'craft_grid', 'smelt', 'check_name'}


def dump(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + '.tmp')
    temporary.write_text(json.dumps(value, sort_keys=True, indent=2) + '\n')
    temporary.replace(path)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def canonical(record):
    return json.dumps({k: v for k, v in record.items()
                       if k not in {'seed', 'elapsed_seconds', 'peak_rss_kib'}},
                      sort_keys=True, separators=(',', ':'))


def source_hashes():
    files = [ROOT / 'config/arbiter_hparams.json', ROOT / 'config/p6_development_tasks.json',
             ROOT / 'src/arbiter/evaluation/p6_execution.py',
             ROOT / 'src/arbiter/evaluation/claim_compiler.py',
             ROOT / 'src/arbiter/adapters/webcraft.py', Path(__file__)]
    files.extend(sorted((ROOT / 'src/arbiter/agent').glob('*.py')))
    files.extend(sorted((WEB / 'sim').glob('*.py')))
    files.extend([WEB / 'oracle/planner.py', WEB / 'config/resource_model.json'])
    return {str(p): sha(p) for p in files}


def prepare():
    inputs = ROOT / 'artifacts/p6_final_all_public/evidence'
    report = preflight(inputs, REPORT / 'clarified_preflight', allow_public_versions=True)
    if report['status'] != 'INPUT_PREFLIGHT_PASS':
        raise RuntimeError('development input preflight failed')
    tasks = development_tasks(ROOT / 'config/p6_development_tasks.json',
                              WEB / 'config/resource_model.json')
    normalizer, compiler = _compiler(WEB)
    bundles = {}
    for path in sorted(inputs.glob('*.json')):
        bundle = json.loads(path.read_text())
        claims, operators = _claims(bundle, normalizer=normalizer)
        state = FilterState(operators, claims)
        bundles[(bundle['task_id'], bundle['condition'])] = {
            'claims': claims, 'operators': operators,
            'no_conflict': not bool(state.conflict_pairs()),
            'discriminating_identifiers': sorted(state.scores()),
            'reader': bundle['reader'], 'evidence_sha256': sha(path),
        }
    fallback = all(not x['discriminating_identifiers'] for x in bundles.values())
    contexts = []
    for task in tasks:
        for condition in ('mixed', 'mismatched_old2new', 'mismatched_new2old'):
            evidence = bundles[(task['id'], condition)]
            for world in ('1.16.5', '1.19', '1.21.11'):
                contexts.append({'task': task, 'condition': condition, 'world': world,
                                 'evidence': evidence, 'fallback': fallback})
    manifest = {
        'source_hashes': source_hashes(), 'compiler': compiler, 'fallback_enabled': fallback,
        'inputs': report['input_hashes'], 'instances': len(contexts),
        'seed': 20261020, 'verification_seed': 20261021,
        'B_grid': [1, 3, 5], 'reader_replay': 'frozen_P0_development_packages',
        'no_conflict_bundles': sum(x['no_conflict'] for x in bundles.values()),
        'bundle_count': len(bundles),
    }
    return contexts, manifest


def execute(payload):
    context, method, budget, seed, out = payload
    # The harness alone owns routing and scoring. Only public objects cross
    # into the policy; there is no task/condition/world parameter on the agent.
    sys.dont_write_bytecode = True
    if str(WEB) not in sys.path:
        sys.path.insert(0, str(WEB))
    from sim import create_simulated_agent
    task, evidence = context['task'], context['evidence']
    out = Path(out)
    out.mkdir(parents=True, exist_ok=True)
    log = out / 'actions.jsonl'
    if log.exists():
        raise RuntimeError('refusing to append to an existing episode log')
    started = time.monotonic()
    initial = Counter()
    for row in task['standard_items'] + task.get('task_supplements', []):
        initial[row['name']] += int(row['count'])
    handle = create_simulated_agent(version=context['world'], log_path=log,
                                    initial_inventory=initial)
    api = PublicSemanticAdapter(handle['api'])
    kit = [ItemStack(name, count) for name, count in initial.items()]
    goal = Goal(task['target']['item'], int(task['target']['count']))
    try:
        if method == 'naive_rag':
            agent = ArbiterAgent(goal, kit, api)
            agent.configure(evidence['operators'], naive_rag(evidence['claims']).belief)
            result = agent.solve(policy=_execution_policy(), strategy=NAIVE_EXECUTION)
        elif method == 'pf_rag':
            agent = PFRAGAgent(goal, kit, api)
            agent.configure_public(evidence['operators'], evidence['claims'], budget=budget,
                                   fallback=context['fallback'])
            result = agent.solve(policy=_execution_policy())
        else:
            raise ValueError('unsupported development method')
        inventory = Counter({r['name']: r['count'] for r in api.inventory()})
        handle['evaluator'].finish_episode()
    finally:
        handle['shutdown']()
    events = [json.loads(l) for l in log.read_text().splitlines() if l.strip()]
    feasible = bool(task['feasible'][context['world']])
    success = ((feasible and result['feasible'] is True and inventory[goal.item] >= goal.count)
               or (not feasible and result['feasible'] is False))
    semantic_actions = sum(e['method'] in SEMANTIC for e in events)
    cost = sum(float(e['cost']['primary']) for e in events)
    if semantic_actions > 90 or cost > 90:
        raise RuntimeError('development action or semantic-cost budget exceeded')
    record = {
        'schema_version': 2, 'record_type': 'episode', 'method': method, 'B': budget,
        'task_id': task['id'], 'world': context['world'], 'condition': context['condition'],
        'reading_mode': 'all_public', 'seed': seed,
        'evidence_sha256': evidence['evidence_sha256'], 'no_conflict': evidence['no_conflict'],
        'fallback_enabled': context['fallback'], 'events': events, 'result': result,
        'true_feasible': feasible, 'reported_feasible': result['feasible'],
        'metrics': {'SR': int(success), 'primary_cost': cost,
                    'semantic_actions': semantic_actions,
                    'probe_count': sum(e.get('intent') == 'probe' for e in events),
                    'search_count': evidence['reader']['search_count'], 'llm_calls': 0},
        'elapsed_seconds': time.monotonic() - started,
        'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    dump(out / 'episode.json', record)
    return record


def job_key(context, method, budget):
    return '__'.join([context['condition'], context['task']['id'], context['world'],
                     method, str(budget)])


def run_jobs(jobs, workers, folder):
    records = []
    with ProcessPoolExecutor(max_workers=workers,
                             mp_context=multiprocessing.get_context('spawn')) as pool:
        futures = {pool.submit(execute, job): job for job in jobs}
        for future in as_completed(futures):
            records.append(future.result())
            dump(folder / 'progress.json', {
                'status': 'RUNNING', 'completed': len(records), 'total': len(jobs),
                'workers': workers, 'updated_at_utc': datetime.now(timezone.utc).isoformat(),
            })
    return sorted(records, key=lambda r: (r['task_id'], r['condition'], r['world'], r['B']))


def pilot(contexts, workers, folder):
    # A fixed first developer instance, never selected based on success.
    context = contexts[0]
    serial, parallel = [], []
    jobs = []
    for method, budget in [('naive_rag', 0), ('pf_rag', 1), ('pf_rag', 3), ('pf_rag', 5)]:
        key = job_key(context, method, budget)
        serial.append(execute((context, method, budget, 20261020, folder / 'serial' / key)))
        jobs.append((context, method, budget, 20261021, folder / 'parallel' / key))
    parallel = run_jobs(jobs, workers, folder)
    serial.sort(key=lambda r: (r['task_id'], r['condition'], r['world'], r['B']))
    equal = all(canonical(a) == canonical(b) for a, b in zip(serial, parallel))
    report = {'passed': equal, 'episodes': 8, 'instance': job_key(context, '', 0),
              'serial_seconds': sum(x['elapsed_seconds'] for x in serial),
              'parallel_episode_seconds': [x['elapsed_seconds'] for x in parallel],
              'selection_outcomes_not_used': True}
    dump(folder / 'pilot.json', report)
    if not equal:
        raise RuntimeError('serial/parallel development pilot mismatch')
    return report


def grid(contexts, workers, folder):
    jobs = []
    for context in contexts:
        for method, budget in [('naive_rag', 0), ('pf_rag', 1), ('pf_rag', 3), ('pf_rag', 5)]:
            jobs.append((context, method, budget, 20261020,
                         folder / 'grid' / job_key(context, method, budget)))
    rows = run_jobs(jobs, workers, folder)
    baseline = {(r['task_id'], r['condition'], r['world']): r
                for r in rows if r['method'] == 'naive_rag'}
    mismatches = []
    compared = 0
    for row in rows:
        if row['method'] == 'pf_rag' and row['no_conflict']:
            compared += 1
            other = baseline[(row['task_id'], row['condition'], row['world'])]
            if row['events'] != other['events']:
                mismatches.append([row['task_id'], row['condition'], row['world'], row['B']])
    scores = []
    for budget in [1, 3, 5]:
        subset = [r for r in rows if r['B'] == budget]
        scores.append({'B': budget, 'n': len(subset),
                       'successes': sum(r['metrics']['SR'] for r in subset),
                       'mean_cost': sum(r['metrics']['primary_cost'] for r in subset) / len(subset)})
    chosen = min(scores, key=lambda r: (-r['successes'], r['mean_cost'], r['B']))['B']
    dump(folder / 'grid_summary.json', {
        'B_scores': scores, 'tentative_B': chosen, 'no_conflict_comparisons': compared,
        'no_conflict_mismatches': mismatches,
        'passed': not mismatches, 'not_a_formal_parameter_freeze': True,
    })
    if mismatches:
        raise RuntimeError('no-conflict equivalence QA failed')
    repeat_jobs = [(context, 'pf_rag', chosen, 20261021,
                    folder / 'repeat' / job_key(context, 'pf_rag', chosen)) for context in contexts]
    repeated = run_jobs(repeat_jobs, workers, folder)
    original = [r for r in rows if r['B'] == chosen]
    equal = all(canonical(a) == canonical(b) for a, b in zip(original, repeated))
    dump(folder / 'selection.json', {
        'B': chosen, 'fallback_enabled': contexts[0]['fallback'], 'stable': equal,
        'development_instances': 72, 'grid_episodes': len(rows),
        'repeat_episodes': len(repeated), 'seed_effect': not equal,
        'no_conflict_comparisons': compared, 'no_conflict_mismatches': mismatches,
        'requires_source_freeze_before_heldout': True,
    })
    if not equal:
        raise RuntimeError('selected development configuration is not deterministic')


def main():
    # Fix interpreter ordering in the parent as well as spawned workers.
    # This is separate from the two registered episode seeds.
    if os.environ.get('PYTHONHASHSEED') != '20261020':
        os.execve(sys.executable, [sys.executable, *sys.argv],
                  {**os.environ, 'PYTHONHASHSEED': '20261020'})
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--phase', choices=['pilot', 'grid'], required=True)
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    os.nice(15)
    for key in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
        os.environ[key] = '1'
    if datetime.now(timezone.utc) >= DEADLINE:
        raise SystemExit('DEVELOPMENT_DEADLINE_REACHED: no new episodes')
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise SystemExit('WAITING_RESOURCES: retry after 600 seconds')
    with Path('/root/autodl-tmp/server.lock').open() as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        if args.output.exists():
            raise SystemExit('output directory exists: no implicit reruns')
        args.output.mkdir(parents=True)
        started = time.monotonic()
        try:
            contexts, manifest = prepare()
            dump(args.output / 'manifest.json', manifest)
            if args.phase == 'pilot':
                pilot(contexts, args.workers, args.output)
            else:
                grid(contexts, args.workers, args.output)
            if source_hashes() != manifest['source_hashes']:
                raise RuntimeError('source changed during development run')
            dump(args.output / 'status.json', {'status': 'COMPLETED',
                 'elapsed_seconds': time.monotonic() - started, 'llm_calls': 0})
        except Exception as exc:
            dump(args.output / 'status.json', {'status': 'STOPPED_QA',
                 'error_type': type(exc).__name__, 'error': str(exc), 'llm_calls': 0})
            raise


if __name__ == '__main__':
    main()
