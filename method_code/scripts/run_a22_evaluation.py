"""A22 held-out orchestration; frozen policy, no new extraction or LLM calls."""
from __future__ import annotations

import argparse
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime, timezone
import fcntl
import hashlib
import json
import multiprocessing
import os
from pathlib import Path
import subprocess
import sys
import time

from arbiter.agent.pf_rag import FilterState
from arbiter.checkpoint import effective_available_bytes
from arbiter.evaluation.main_matrix import MAIN_CELLS
from arbiter.evaluation.p6_execution import _claims, _compiler
from arbiter.pinning import build_pin
from run_a22_development import execute, canonical, dump, sha, ROOT, WEB
from run_main import load_evaluation_tasks


BASE = ROOT / 'artifacts/post_review/a22_pf_rag/evaluation'
REPORT = ROOT / 'reports/main/post_review/a22_pf_rag/evaluation'
FREEZE = ROOT / 'config/a22_pf_rag_frozen.json'
SEEDS = (20261020, 20261021)


def jsonl(path):
    with path.open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def verify_policy():
    config = json.loads(FREEZE.read_text())
    path = ROOT / config['development_source_manifest']
    if sha(path) != config['development_source_manifest_sha256']:
        raise RuntimeError('frozen development manifest changed')
    hashes = json.loads(path.read_text())['source_hashes']
    original_root = Path('/root/autodl-tmp/arbiter')
    def dependency_path(name):
        path = Path(name)
        return ROOT / path.relative_to(original_root) if path.is_relative_to(original_root) else path
    changed = [name for name, expected in hashes.items() if sha(dependency_path(name)) != expected]
    if changed:
        raise RuntimeError('frozen method/dependency changed: ' + ', '.join(changed))
    if config['B'] != 1 or config['fallback_enabled'] is not False:
        raise RuntimeError('unexpected frozen PF parameters')
    return config


def prepare():
    config = verify_policy()
    normalizer, compiler = _compiler(WEB)
    tasks = load_evaluation_tasks(WEB / 'data/tasks.jsonl', WEB / 'config/resource_model.json')
    paths = {str(FREEZE): sha(FREEZE)}
    for name in ['data/tasks.jsonl', 'data/ext_v2/ext_v2_tasks_20261016.jsonl',
                 'config/ext_v2/web_cells.json', 'config/ext_v2/freeze_manifest.json',
                 'data/conflicts.jsonl', 'harness/config/arena.json']:
        paths[str(WEB / name)] = sha(WEB / name)
    cache, bundle_metadata = {}, []

    def evidence(path, mode):
        key = str(path)
        if key not in cache:
            bundle = json.loads(path.read_text())
            if bundle['reading_mode'] != mode:
                raise RuntimeError('evidence reading mode mismatch')
            reader = bundle['reader']
            if not 0 <= reader['search_count'] <= 60:
                raise RuntimeError('shared reader exceeds frozen search budget')
            claims, operators = _claims(bundle, normalizer=normalizer)
            state = FilterState(operators, claims)
            cache[key] = {'claims': claims, 'operators': operators,
                          'no_conflict': not bool(state.conflict_pairs()),
                          'reader': reader, 'evidence_sha256': sha(path)}
            paths[key] = sha(path)
            bundle_metadata.append({'path': key, 'sha256': sha(path), 'reading_mode': mode,
                                    'search_rows': len(bundle['search_rows']),
                                    'compiled_candidates': len(operators),
                                    'search_count': reader['search_count']})
        return cache[key]

    contexts = []
    for mode in ['all_public', 'llm_only', 'oracle_reading']:
        for condition, world in MAIN_CELLS:
            for task in tasks:
                if mode == 'oracle_reading':
                    path = ROOT / f'artifacts/post_review/oracle_reading/evidence/{condition}__{task["id"]}.json'
                else:
                    path = ROOT / f'artifacts/main/{mode}/evidence/{condition}__{task["id"]}__20261006.json'
                    other = path.with_name(path.name.replace('20261006', '20261007'))
                    if sha(path) != sha(other):
                        raise RuntimeError('historical seed evidence differs; cannot choose a favorable source')
                    paths[str(other)] = sha(other)
                contexts.append({'scope': 'main', 'mode': mode, 'condition': condition,
                                 'world': world, 'task': task, 'family_id': task['id'],
                                 'fallback': config['fallback_enabled'],
                                 'evidence': evidence(path, mode), 'evidence_path': str(path)})
    ext_config = json.loads((WEB / 'config/ext_v2/web_cells.json').read_text())
    raw_tasks = {r['id']: r for r in jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    standard = json.loads((WEB / 'config/resource_model.json').read_text())['standard_inventory']
    if len(ext_config['web_task_ids']) != 12 or len(ext_config['cells']) != 5:
        raise RuntimeError('EXT-v2 scope differs from registered 12 x 5')
    freeze = json.loads((WEB / 'config/ext_v2/freeze_manifest.json').read_text())
    for name, digest in freeze['inputs'].items():
        if sha(WEB / name) != digest:
            raise RuntimeError('EXT-v2 frozen input hash changed: ' + name)
        paths[str(WEB / name)] = digest
    for cell in ext_config['cells']:
        for task_id in sorted(ext_config['web_task_ids']):
            raw = raw_tasks[task_id]
            task = {k: raw[k] for k in ['id', 'target', 'feasible', 'conflict_ids']}
            task.update(standard_items=standard, task_supplements=raw.get('task_supplements', []))
            path = WEB / f'reports/paperA/ext_v2/web_run/evidence/{cell["condition"]}__{task_id}__20261016.json'
            contexts.append({'scope': 'ext_v2', 'mode': 'all_public', 'task': task,
                             'condition': cell['condition'], 'world': cell['world'],
                             'family_id': raw['independent_family_id'], 'fallback': False,
                             'evidence': evidence(path, 'all_public'), 'evidence_path': str(path)})
    if len(contexts) != 1182:
        raise RuntimeError('formal matrix coverage mismatch')
    manifest = {'freeze_commit': '74b38fb', 'freeze_sha256': sha(FREEZE),
                'source_hashes': paths, 'compiler': compiler, 'seeds': list(SEEDS),
                'instances': len(contexts), 'episodes': len(contexts) * 2,
                'B': 1, 'fallback_enabled': False, 'python_hash_seed': '20261020',
                'webcraft_commit': subprocess.check_output(
                    ['git', '-C', str(WEB), 'rev-parse', 'HEAD'], text=True).strip(),
                'evidence_packages': bundle_metadata,
                'created_at_utc': datetime.now(timezone.utc).isoformat(),
                'retrieval': 'replay_frozen_shared_reader_output', 'new_retrieval_calls': 0,
                'new_llm_calls': 0, 'new_extraction_calls': 0,
                'policy_information': 'compiled_public_candidates_and_own_observations_only'}
    return contexts, manifest


def key(context):
    return '__'.join([context['scope'], context['mode'], context['condition'],
                     context['world'], context['task']['id']])


def formal_job(payload):
    context, seed, folder = payload
    result = execute((context, 'pf_rag', 1, seed, Path(folder) / 'raw'))
    result.update(scope=context['scope'], reading_mode=context['mode'],
                  family_id=context['family_id'], reader=context['evidence']['reader'],
                  evidence_path=context['evidence_path'], runtime_search_calls=0,
                  method_version='a22-pf-rag-frozen-B1')
    result['policy_trace'] = result['result']['policy_trace']
    dump(Path(folder) / 'episode.json', result)
    return result


def verify_results(base):
    pairs = {}
    for path in sorted((base / 'episodes').glob('**/episode.json')):
        if path.parent.name == 'raw':
            continue
        row = json.loads(path.read_text())
        k = (row['scope'], row['reading_mode'], row['condition'], row['world'], row['task_id'])
        if row['seed'] in pairs.setdefault(k, {}):
            raise RuntimeError('duplicate formal episode')
        pairs[k][row['seed']] = row
    differences = []
    if len(pairs) != 1182 or any(set(v) != set(SEEDS) for v in pairs.values()):
        raise RuntimeError('formal result coverage incomplete')
    for k, rows in sorted(pairs.items()):
        if canonical(rows[SEEDS[0]]) != canonical(rows[SEEDS[1]]):
            differences.append(k)
    report = {'instances': len(pairs), 'episodes': len(pairs) * 2,
              'canonical_seed_identical': not differences,
              'mismatched_instances': differences, 'seed_replicas_are_not_independent': True}
    dump(base / 'determinism.json', report)
    return report


def main():
    if os.environ.get('PYTHONHASHSEED') != '20261020':
        os.execve(sys.executable, [sys.executable, *sys.argv],
                  {**os.environ, 'PYTHONHASHSEED': '20261020'})
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, default=BASE)
    parser.add_argument('--workers', type=int, choices=range(1, 5), default=4)
    parser.add_argument('--prepare-only', action='store_true')
    args = parser.parse_args()
    sys.dont_write_bytecode = True
    os.nice(15)
    for k in ['OPENBLAS_NUM_THREADS', 'OMP_NUM_THREADS', 'MKL_NUM_THREADS']:
        os.environ[k] = '1'
    args.output.mkdir(parents=True, exist_ok=True)
    started = time.monotonic()
    with (args.output / 'run.lock').open('a+') as own_lock:
        fcntl.flock(own_lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        with Path('/root/autodl-tmp/server.lock').open() as server_lock:
            while True:
                enough = effective_available_bytes() >= int(1.8 * 1024**3)
                try:
                    if not enough:
                        raise BlockingIOError
                    fcntl.flock(server_lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
                    break
                except BlockingIOError:
                    dump(args.output / 'status.json', {'status': 'WAITING_RESOURCES',
                         'memory_ok': enough, 'next_poll_seconds': 600})
                    time.sleep(600)
            try:
                contexts, manifest = prepare()
                pin = build_pin(WEB)
                existing = args.output / 'manifest.json'
                if existing.exists():
                    previous = json.loads(existing.read_text())
                    if previous['source_hashes'] != manifest['source_hashes']:
                        raise RuntimeError('input hashes differ from existing run')
                    manifest = previous
                else:
                    dump(existing, manifest)
                    dump(args.output / 'webcraft_pin.json', pin)
                if json.loads((args.output / 'webcraft_pin.json').read_text()) != pin:
                    raise RuntimeError('WebCraft pin changed')
                if args.prepare_only:
                    dump(args.output / 'status.json', {'status': 'PREPARED', 'episodes': 2364})
                    return
                jobs = []
                completed = 0
                for seed in SEEDS:
                    for context in contexts:
                        folder = args.output / 'episodes' / str(seed) / key(context)
                        if (folder / 'episode.json').exists():
                            completed += 1
                        elif (folder / 'raw').exists():
                            raise RuntimeError('partial episode exists; refusing implicit rerun')
                        else:
                            jobs.append((context, seed, folder))
                def progress(status='RUNNING'):
                    dump(args.output / 'status.json', {'status': status,
                         'completed': completed, 'total': 2364, 'workers': args.workers,
                         'pid': os.getpid(), 'llm_calls': 0,
                         'updated_at_utc': datetime.now(timezone.utc).isoformat(),
                         'elapsed_seconds': time.monotonic() - started})
                progress()
                with ProcessPoolExecutor(max_workers=args.workers,
                        mp_context=multiprocessing.get_context('spawn')) as pool:
                    futures = [pool.submit(formal_job, job) for job in jobs]
                    try:
                        for future in as_completed(futures):
                            future.result()
                            completed += 1
                            progress()
                    except Exception:
                        for future in futures:
                            future.cancel()
                        raise
                determinism = verify_results(args.output)
                verify_policy()
                if build_pin(WEB) != pin:
                    raise RuntimeError('WebCraft changed during run')
                progress('COMPLETED' if determinism['canonical_seed_identical'] else 'FAILED_DETERMINISM')
            except Exception as exc:
                dump(args.output / 'status.json', {'status': 'STOPPED_QA',
                     'error_type': type(exc).__name__, 'error': str(exc),
                     'elapsed_seconds': time.monotonic() - started, 'llm_calls': 0})
                raise


if __name__ == '__main__':
    main()
