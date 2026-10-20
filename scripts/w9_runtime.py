#!/usr/bin/env python3
"""Offline, relocatable release replay and exact per-episode comparison."""
from __future__ import annotations

import argparse
import csv
import gzip
import hashlib
import json
import os
import resource
import importlib.metadata
import socket
import sys
import time
from collections import Counter, defaultdict
from decimal import Decimal
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(ROOT / 'method_code/src'), str(ROOT)]
from arbiter.evaluation import p6_execution as p6
from arbiter.evaluation.p6_matrix import P6Job
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_resource_nodes
from arbiter.evaluation.measurement_v3 import (
    classify_actions_v3, intrinsic_discriminative, result_mismatch,
)
from arbiter.evaluation.post_review_measurement import terminal
from arbiter.agent.aggregation import operator_from_assertion
import arbiter.adapters.webcraft as webcraft_adapter
import arbiter.adapters.claims as claims_adapter
import arbiter.adapters.search as search_adapter


def read_jsonl(path):
    opener = gzip.open if str(path).endswith('.gz') else open
    with opener(path, 'rt') as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_csv(path, records):
    records = list(records)
    if not records:
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in records for key in row))
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(records)


def sha(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_manifest():
    manifest = ROOT / 'MANIFEST.sha256'
    checked = 0
    for line in manifest.read_text().splitlines():
        digest, name = line.split('  ', 1)
        path = ROOT / name
        if sha(path) != digest:
            raise RuntimeError(f'package hash mismatch: {name}')
        checked += 1
    return checked


def setup():
    checked = verify_manifest()
    restricted = ROOT / 'docs/RESTRICTED_FILES.json'
    if restricted.is_file():
        for entry in json.loads(restricted.read_text()):
            path = ROOT / entry['path']
            if not path.is_file() or sha(path) != entry['sha256']:
                raise RuntimeError(f"restricted asset missing or altered: {entry['path']}")
    def verified_package(*args, **kwargs):
        return {'package_manifest_verified': True, 'checked_files': checked,
                'experiment_ready': True, 'claims_cache_p0_ready': True,
                'claims_cache_v4_ready': True, 'backend_ready': True}
    # Package manifests replace repository git-HEAD verification only. Method,
    # compiler, planner and simulator execution are not monkey-patched.
    for module in (p6, webcraft_adapter, claims_adapter, search_adapter):
        module.verify_webcraft_pin = verified_package
    class ForbiddenGateway:
        def __init__(self, *args, **kwargs):
            raise RuntimeError('release replay forbids LLM construction')
    p6.GatewayClient = ForbiddenGateway
    def deny_network(*args, **kwargs):
        raise RuntimeError('release replay forbids network access')
    socket.create_connection = deny_network
    socket.socket.connect = deny_network
    for var in ('OMP_NUM_THREADS', 'OPENBLAS_NUM_THREADS', 'MKL_NUM_THREADS'):
        os.environ[var] = '1'
    prior = json.loads((ROOT / 'assets/prior/frozen_prior_bundle.json').read_text())
    prior = {'operators': [operator_from_assertion(row) for row in prior['assertions']],
             'samples': prior['samples']}
    conflicts = {row['id']: row for row in read_jsonl(ROOT / 'data/conflicts.jsonl')}
    nodes = load_resource_nodes(ROOT / 'harness/config/arena.json')
    return prior, conflicts, nodes


def projected_task(task, standard):
    return {'id': task['id'], 'target': task['target'], 'feasible': task['feasible'],
            'conflict_ids': task['conflict_ids'], 'standard_items': standard,
            'task_supplements': task.get('task_supplements', [])}


def metric(record, conflicts, nodes, target):
    events = record['events']
    semantic_indices = [i for i, event in enumerate(events) if event.get('method') in ACTION_METHODS]
    mechanisms = record['used_mechanism_ids']
    if len(semantic_indices) != len(mechanisms):
        raise RuntimeError('action/mechanism alignment changed')
    stale_map = {i: [mid] for i, mid in zip(semantic_indices, mechanisms)
                 if mid in conflicts and conflicts[mid]['truth_vector'].get(record['world']) is False
                 and any(v is True for v in conflicts[mid]['truth_vector'].values())}
    annotations = classify_actions_v3(events, stale_by_event=stale_map, conflicts=conflicts,
                                     resource_nodes=nodes, target=target,
                                     episode_success=bool(record['metrics']['SR']))
    query = action = 0
    for row in annotations:
        event = events[row['event_index']]
        specific = intrinsic_discriminative(event, row['stale_mechanism_ids'], world=record['world'],
                                            conflicts=conflicts, resource_nodes=nodes) or bool(row['referenced_at'])
        if specific:
            if event['method'] == 'check_name':
                query = 1
            else:
                action = 1
    label = terminal(record)
    feasible = bool(record['true_feasible'])
    return {
        'terminal': label, 'SR': int(record['metrics']['SR']),
        'overconservative': int(feasible and label in {'plan_not_found', 'incorrectly_infeasible'}),
        'execution_failure': int(feasible and label == 'execution_failed'),
        'plan_acceptance': int(record['metrics']['HTR_commit']),
        'stale_contact': int(record['metrics']['HTR_all']),
        'specific_query': query, 'specific_action': action,
        'semantic_actions': len(semantic_indices),
        'semantic_cost': str(Decimal(str(record['metrics']['primary_cost']))),
        'search_calls': int(record['reader'].get('search_count', 0)),
    }


def action_signature(record, remove_nodes=False):
    result = []
    for event in record['events']:
        if event.get('method') not in ACTION_METHODS:
            continue
        params = dict(event.get('params', {}))
        if remove_nodes and event['method'] == 'mine':
            for key in ('nodeId', 'node_id', 'node'):
                params.pop(key, None)
        result.append(json.dumps({'method': event['method'], 'params': params,
                                  'intent': event.get('intent')}, sort_keys=True))
    return result


def action_difference(new, old):
    if action_signature(new) == action_signature(old):
        return ''
    if action_signature(new, True) == action_signature(old, True):
        return 'equivalent_resource_node'
    if (Counter(new['used_mechanism_ids']) == Counter(old['used_mechanism_ids'])
            and Counter(action_signature(new)) == Counter(action_signature(old))):
        return 'dependency_equivalent_tie'
    return 'other'


def load_evidence(scope):
    output = {}
    for path in (ROOT / f'assets/evidence/{scope}.jsonl.gz',
                 ROOT / f'assets/restricted/evidence/{scope}.jsonl.gz'):
        if not path.is_file():
            continue
        for row in read_jsonl(path):
            output[(row['mode'], row['condition'], row['task_id'])] = row['bundle']
    return output


def check_imports():
    for name, module in list(sys.modules.items()):
        if name.split('.')[0] in {'arbiter', 'sim', 'oracle', 'corpus'}:
            location = getattr(module, '__file__', None)
            if location and not Path(location).resolve().is_relative_to(ROOT):
                raise RuntimeError(f'external repository import: {name}')


def replay(scope, output, limit=None, gate=False):
    prior, conflicts, nodes = setup()
    tasks = {row['id']: row for row in read_jsonl(ROOT / ('data/tasks.jsonl' if scope == 'main'
                                                       else 'data/ext_v2/ext_v2_tasks_20261016.jsonl'))}
    standard = json.loads((ROOT / 'config/resource_model.json').read_text())['standard_inventory']
    evidence = load_evidence(scope)
    comparisons = []
    started = time.monotonic()
    output.mkdir(parents=True, exist_ok=True)
    baseline_path = ROOT / f'assets/frozen/{scope}.jsonl.gz'
    run_path = output / f'{scope}_replayed.jsonl'
    summary = Counter()
    frozen_metrics = []
    with run_path.open('w') as log:
        for old in read_jsonl(baseline_path):
            if gate and (old['reading_mode'] != 'all_public' or old['condition'] not in {
                    'mismatched_old2new', 'matched_1.21.11', 'mismatched_new2old', 'matched_1.16.5'}):
                continue
            if limit is not None and len(comparisons) >= limit:
                break
            mode = old['reading_mode']
            key = (mode, old['condition'], old['task_id'])
            if key not in evidence:
                raise RuntimeError(f'missing packaged evidence: {key}')
            raw_task = tasks[old['task_id']]
            task = projected_task(raw_task, standard)
            bundle = evidence[key]
            vectors = {mid: row['truth_vector'] for mid, row in conflicts.items()}
            if scope == 'cds':
                private = json.loads((ROOT / f'evaluator/cds/{old["cds_instance_id"]}.json').read_text())
                for candidate in private['boundary_truth']:
                    vec = {world: bool(candidate['truth']) for world in ('1.16.5', '1.19', '1.21.11')}
                    vec[private['comparison_world']] = not bool(candidate['truth'])
                    vectors[candidate['mechanism_id']] = vec
            job = P6Job(old['condition'], old['task_id'], old['world'], old['method'], int(old['seed']))
            try:
                new = p6.run_episode(job, task, bundle, root=ROOT, pin_path=ROOT / 'MANIFEST.sha256',
                                      index_dir=str(ROOT / 'corpus/indexes/formal_p1'),
                                      output_dir=output / 'runtime', conflict_vectors=vectors,
                                      preview=False, prior_bundle=prior)
                old_metrics = metric(old, conflicts, nodes, task['target']['item'])
                new_metrics = metric(new, conflicts, nodes, task['target']['item'])
                changed = [key for key in old_metrics if new_metrics[key] != old_metrics[key]]
                multiset_equal = Counter(old['used_mechanism_ids']) == Counter(new['used_mechanism_ids'])
                kind = action_difference(new, old)
                status = 'metric_difference' if changed else 'equal'
                if gate and not multiset_equal:
                    changed.append('mechanism_multiset')
                    status = 'metric_difference'
                record = {'scope': scope, 'reading_mode': mode, 'method': old['method'],
                          'condition': old['condition'], 'world': old['world'], 'task_id': old['task_id'],
                          'status': status, 'changed_metrics': ';'.join(changed),
                          'action_only_difference': kind if not changed else '',
                          'action_difference_type': kind, 'mechanism_multiset_equal': int(multiset_equal)}
                for field in old_metrics:
                    record[f'frozen_{field}'] = old_metrics[field]
                    record[f'replayed_{field}'] = new_metrics[field]
                frozen_metrics.append({'scope': scope, 'reading_mode': mode, 'method': old['method'],
                                       'condition': old['condition'], 'world': old['world'],
                                       'task_id': old['task_id'], **old_metrics})
                if any(event.get('cost', {}).get('model_calls', 0) for event in new['events']):
                    raise RuntimeError('unexpected model call count')
                log.write(json.dumps(new, sort_keys=True) + '\n')
            except Exception as exc:
                record = {'scope': scope, 'reading_mode': mode, 'method': old['method'],
                          'condition': old['condition'], 'world': old['world'], 'task_id': old['task_id'],
                          'status': 'runtime_error', 'changed_metrics': type(exc).__name__ + ': ' + str(exc)}
            comparisons.append(record)
            summary[record['status']] += 1
            if len(comparisons) % 100 == 0:
                print(json.dumps({'scope': scope, 'completed': len(comparisons),
                                  'summary': dict(summary), 'elapsed_s': round(time.monotonic()-started, 2)}), flush=True)
            if gate and record['status'] != 'equal':
                break
    write_csv(output / f'{scope}_comparison.csv', comparisons)
    write_csv(output / f'{scope}_frozen_metrics.csv', frozen_metrics)
    grouped = defaultdict(list)
    for row in comparisons:
        grouped[(row['reading_mode'], row['method'])].append(row)
    write_csv(output / f'{scope}_summary.csv', [
        {'scope': scope, 'reading_mode': mode, 'method': method, 'compared': len(values),
         'metric_differences': sum(r['status'] == 'metric_difference' for r in values),
         'runtime_errors': sum(r['status'] == 'runtime_error' for r in values),
         'action_only_differences': sum(bool(r.get('action_only_difference')) for r in values)}
        for (mode, method), values in sorted(grouped.items())])
    check_imports()
    expected = 1360 if gate else {'main': 11220, 'ext_v2': 600, 'cds': 2600}[scope]
    result = {'scope': scope, 'expected': expected, 'compared': len(comparisons),
              'summary': dict(summary), 'action_only_differences': sum(bool(r.get('action_only_difference')) for r in comparisons),
              'elapsed_s': round(time.monotonic()-started, 3), 'python': sys.version,
              'PYTHONHASHSEED': os.environ.get('PYTHONHASHSEED'),
              'peak_rss_kib': resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
              'package_manifest_sha256': sha(ROOT / 'MANIFEST.sha256'),
              'dependencies': {name: importlib.metadata.version(name)
                               for name in ('numpy','scipy','scikit-learn','pulp','pytest')},
              'isolated_imports': True, 'gate': gate,
              'gate_passed': bool(gate and len(comparisons)==expected and summary['equal']==expected)}
    (output / f'{scope}_result.json').write_text(json.dumps(result, indent=2, sort_keys=True)+'\n')
    print(json.dumps(result), flush=True)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--scope', choices=('main', 'ext_v2', 'cds'), default='main')
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--limit', type=int)
    parser.add_argument('--gate', action='store_true')
    args = parser.parse_args()
    replay(args.scope, args.output, args.limit, args.gate)


if __name__ == '__main__':
    main()
