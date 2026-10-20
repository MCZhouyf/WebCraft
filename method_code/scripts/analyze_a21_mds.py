#!/usr/bin/env python3
"""Offline A21 MDS scorer and family-cluster bootstrap exporter."""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

from arbiter.evaluation.external_scoring import ACTION_METHODS, _matches, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import classify_actions_v3, intrinsic_discriminative, prerequisite_ready
from scripts.run_measurement_v3d_20261015 import v3d_rows

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
BASE = ROOT / 'artifacts/post_review/a21_mds_20261018/formal_run'
REPORT = ROOT / 'reports/main/post_review/a21_mds_20261018'
SEED, DRAWS = 20261018, 10_000


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(dict.fromkeys(key for row in rows for key in row))
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader(); writer.writerows(rows)


def stale_map(events: list[dict[str, Any]], world: str, conflicts: dict[str, dict[str, Any]], nodes: dict[str, str]) -> dict[int, list[str]]:
    result: dict[int, list[str]] = {}
    for index, event in enumerate(events):
        if event.get('method') not in ACTION_METHODS:
            continue
        ids = [key for key, row in conflicts.items()
               if _matches(event, row['assertion'], nodes)
               and row['truth_vector'].get(world) is False
               and any(value is True for value in row['truth_vector'].values())]
        if ids: result[index] = sorted(ids)
    return result


def score(record: dict[str, Any], conflicts: dict[str, dict[str, Any]], nodes: dict[str, str]) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events = jsonl(ROOT / record['action_log'])
    mapping = stale_map(events, record['target_world'], conflicts, nodes)
    classified = classify_actions_v3(events, stale_by_event=mapping, conflicts=conflicts, resource_nodes=nodes,
                                     target=record['target_item'], episode_success=bool(record['success']))
    annotations = []
    for row in classified:
        event = events[row['event_index']]
        disc = int(bool(row['referenced_at']) or intrinsic_discriminative(
            event, row['stale_mechanism_ids'], world=record['target_world'],
            conflicts=conflicts, resource_nodes=nodes))
        annotations.append({**row, 'instance_id': record['instance_id'], 'condition': record['condition'],
                            'discriminative': disc, 'prerequisite_ready': prerequisite_ready(events, row['event_index']),
                            'contact_type': 'query' if event.get('method') == 'check_name' else 'action'})
    # The frozen v3-d implementation consumes CSV-shaped evidence. Reuse it
    # rather than treating every v3 commitment as FIFO-supported propagation.
    key = ('a21', 'react', record['condition'], record['target_world'], record['task_id'], record['seed'])
    inputs = [{**row, 'setting': 'a21', 'method': 'react', 'action_method': row['method'],
               'world': record['target_world'], 'task_id': record['task_id'], 'seed': record['seed'],
               **{name: json.dumps(row[name]) for name in ('predicted_products', 'referenced_at', 'repeated_at')}}
              for row in annotations]
    annotations = v3d_rows(inputs, {key: record}, pipeline=False)
    disc = [row for row in annotations if row['discriminative']]
    denied = [row for row in disc if row['mismatch']]
    terminal = record['gold_score']['terminal']
    out = {
        'instance_id': record['instance_id'], 'source_task_id': record['source_task_id'],
        'target_item': record['target_item'],
        'family_id': record['family_id'], 'condition': record['condition'], 'world': record['target_world'],
        'comparison_world': record['comparison_world'], 'true_feasible': int(record['true_feasible']),
        'success': int(record['success']), 'feasible_success': int(record['success'] and record['true_feasible']),
        'overconservative': int(record['true_feasible'] and record['reported_feasible'] is False),
        'HTR_all': int(record['metrics']['HTR_all']), 'stale_contact': int(bool(disc)),
        'query_contact': int(any(row['contact_type'] == 'query' for row in disc)),
        'action_contact': int(any(row['contact_type'] == 'action' for row in disc)),
        'v3d_lower': int(any(row['v3d_propagation'] for row in denied)),
        'v3d_upper': int(any(row['v3d_propagation'] or row['v3_label'] == 'undetermined' for row in denied)),
        'semantic_actions': int(record['gold_score']['semantic_actions']), 'model_calls': int(record['model_calls']),
        'input_tokens': int(record['input_tokens']), 'output_tokens': int(record['output_tokens']),
        'primary_cost': record['primary_cost'], 'terminal': terminal,
        'model_output_error': record['model_output_error'] or '',
        'action_log': record['action_log'], 'prompt_log': record['prompt_log'],
    }
    return out, annotations


def ci_by_family(rows: list[dict[str, Any]], metric: str, *, feasible_only: bool = False) -> tuple[float | str, float | str, float | str, int]:
    data = [row for row in rows if not feasible_only or row['true_feasible']]
    if not data: return '', '', '', 0
    families: dict[str, list[float]] = defaultdict(list)
    for row in data: families[row['family_id']].append(float(row[metric]))
    groups = list(families.values())
    point = mean(float(row[metric]) for row in data)
    if len(groups) < 2: return point, '', '', len(groups)
    generator = random.Random(SEED)
    draws = []
    for _ in range(DRAWS):
        sample = generator.choices(groups, k=len(groups))
        draws.append(sum(sum(group) for group in sample) / sum(len(group) for group in sample))
    draws.sort()
    return point, draws[int(.025 * DRAWS)], draws[int(.975 * DRAWS) - 1], len(groups)


def paired_ci(left: list[dict[str, Any]], right: list[dict[str, Any]], metric: str, *, feasible_only: bool = False) -> tuple[float | str, float | str, float | str, int]:
    a = {row['instance_id']: row for row in left if not feasible_only or row['true_feasible']}
    b = {row['instance_id']: row for row in right if not feasible_only or row['true_feasible']}
    pairs = [a[key] for key in sorted(set(a) & set(b))]
    if not pairs: return '', '', '', 0
    by_family: dict[str, list[float]] = defaultdict(list)
    for row in pairs: by_family[row['family_id']].append(float(row[metric]) - float(b[row['instance_id']][metric]))
    groups = list(by_family.values())
    point = mean(float(row[metric]) - float(b[row['instance_id']][metric]) for row in pairs)
    if len(groups) < 2: return point, '', '', len(pairs)
    generator = random.Random(SEED)
    draws = []
    for _ in range(DRAWS):
        sample = generator.choices(groups, k=len(groups))
        draws.append(sum(sum(group) for group in sample) / sum(len(group) for group in sample))
    draws.sort()
    return point, draws[int(.025 * DRAWS)], draws[int(.975 * DRAWS) - 1], len(pairs)


def semantic_sequence(path: str, nodes: dict[str, str]) -> list[str]:
    out = []
    for event in jsonl(ROOT / path):
        if event.get('method') not in ACTION_METHODS: continue
        params = dict(event.get('params', {}))
        if event['method'] == 'mine': params['node_type'] = nodes.get(str(params.pop('nodeId', '')), '')
        out.append(json.dumps([event['method'], params, event.get('success')], sort_keys=True))
    return out


def main() -> int:
    records = jsonl(BASE / 'episodes.jsonl')
    if len(records) != 128 or len({(r['condition'], r['instance_id']) for r in records}) != 128:
        raise RuntimeError('A21 formal coverage is incomplete')
    conflicts = {row['id']: row for row in load_conflicts(WEB / 'data/conflicts.jsonl')}
    nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
    rows, actions = [], []
    for record in records:
        row, annotation = score(record, conflicts, nodes)
        rows.append(row); actions.extend(annotation)
    write_csv(REPORT / 'per_episode.csv', rows)
    write_csv(REPORT / 'stale_action_annotations.csv', actions or [{'instance_id': '', 'condition': ''}])
    table = []
    for condition in ('K', 'K+S', 'K+N', 'empty'):
        subset = [row for row in rows if row['condition'] == condition]
        result = {'condition': condition, 'n': len(subset), 'feasible_n': sum(row['true_feasible'] for row in subset)}
        for metric, feas in (('success', False), ('feasible_success', True), ('stale_contact', False),
                             ('query_contact', False), ('action_contact', False), ('v3d_lower', False),
                             ('v3d_upper', False), ('overconservative', True)):
            point, lo, hi, families = ci_by_family(subset, metric, feasible_only=feas)
            result[metric] = point; result[metric + '_ci_low'] = lo; result[metric + '_ci_high'] = hi
            result[metric + '_families'] = families
        table.append(result)
    write_csv(REPORT / 'main_table.csv', table)
    contrasts = []
    by_condition = {condition: [row for row in rows if row['condition'] == condition]
                    for condition in ('K', 'K+S', 'K+N', 'empty')}
    for left, right, metrics in (
        ('K+S', 'K+N', ('success', 'stale_contact', 'query_contact', 'action_contact', 'v3d_lower')),
        ('empty', 'K', ('success', 'feasible_success')),
    ):
        for metric in metrics:
            point, lo, hi, n = paired_ci(by_condition[left], by_condition[right], metric,
                                         feasible_only=(metric == 'feasible_success'))
            contrasts.append({'contrast': f'{left} - {right}', 'metric': metric, 'estimate': point,
                              'ci_low': lo, 'ci_high': hi, 'paired_n': n,
                              'families': len({row['family_id'] for row in by_condition[left]
                                               if metric != 'feasible_success' or row['true_feasible']}),
                              'seed': SEED, 'draws': DRAWS})
    write_csv(REPORT / 'paired_contrasts.csv', contrasts)
    divergence = []
    keyed = {(row['condition'], row['instance_id']): row for row in rows}
    for instance in sorted({row['instance_id'] for row in rows}):
        left, right = keyed[('K+S', instance)], keyed[('K', instance)]
        seq_l, seq_r = semantic_sequence(left['action_log'], nodes), semantic_sequence(right['action_log'], nodes)
        index = next((i for i, pair in enumerate(zip(seq_l, seq_r), 1) if pair[0] != pair[1]), None)
        if index is None and len(seq_l) != len(seq_r): index = min(len(seq_l), len(seq_r)) + 1
        divergence.append({'instance_id': instance, 'family_id': left['family_id'], 'first_divergence_step': index or '',
                           'K+S_semantic_actions': len(seq_l), 'K_semantic_actions': len(seq_r),
                           'same_semantic_sequence': int(index is None)})
    write_csv(REPORT / 'first_action_divergence.csv', divergence)
    ledger = [row for path in BASE.glob('**/worker_*/ledger.jsonl') for row in jsonl(path)]
    usage = {'episodes': len(rows), 'successful_generations': sum(row.get('status') == 'ok' for row in ledger),
             'input_tokens': sum(row.get('input_tokens', 0) for row in ledger if row.get('status') == 'ok'),
             'output_tokens': sum(row.get('output_tokens', 0) for row in ledger if row.get('status') == 'ok'),
             'estimated_CNY': sum(row.get('cost') or 0 for row in ledger if row.get('status') == 'ok'),
             'cost_note': 'estimate using registered transport prices; not an invoice', 'seed': SEED}
    (REPORT / 'cost.json').write_text(json.dumps(usage, indent=2, sort_keys=True) + '\n')
    print(json.dumps({'episodes': len(rows), 'table_rows': len(table), 'contrasts': len(contrasts)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
