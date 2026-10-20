"""Offline A22 export; all gold/scoring access occurs after policy execution."""
from __future__ import annotations

import argparse
import ast
from collections import Counter, defaultdict
import csv
import json
from pathlib import Path
import re
import time

import numpy as np

from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import classify_actions_v3
from arbiter.evaluation.main_matrix import MAIN_CELLS
from run_measurement_v3_20261014 import external_stale_map, annotate_discriminative
from run_a22_evaluation import BASE, REPORT, ROOT, WEB, SEEDS, jsonl, verify_policy
from run_a22_development import dump, sha


METRICS = ('SR', 'OC', 'EF', 'stale_contact', 'query_contact', 'action_contact',
           'planning_adoption', 'probe_count', 'semantic_actions', 'semantic_cost')


def csv_out(path, rows, fields=None):
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = list(dict.fromkeys(k for row in rows for k in row))
    with path.open('w', newline='') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def paired_ci(values, draws=10000, seed=20261020):
    grouped = defaultdict(list)
    for cluster, value in values:
        grouped[cluster].append(float(value))
    keys = sorted(grouped)
    result = {'estimate': None, 'ci_low': None, 'ci_high': None,
              'n': len(values), 'clusters': len(keys), 'draws': draws, 'seed': seed}
    if not values:
        return {**result, 'status': 'NOT_REPORTABLE_no_eligible_pairs'}
    sums = np.array([sum(grouped[k]) for k in keys])
    counts = np.array([len(grouped[k]) for k in keys])
    result['estimate'] = float(sums.sum() / counts.sum())
    if len(keys) < 2:
        return {**result, 'status': 'POINT_ONLY_less_than_two_clusters'}
    picks = np.random.default_rng(seed).integers(0, len(keys), (draws, len(keys)))
    sample = sums[picks].sum(axis=1) / counts[picks].sum(axis=1)
    return {**result, 'ci_low': float(np.quantile(sample, .025)),
            'ci_high': float(np.quantile(sample, .975)), 'status': 'OK'}


def normalized_actions(record):
    return [{k: e.get(k) for k in ['method', 'params', 'result', 'cost']}
            for e in record['events'] if e['method'] in {'mine', 'craft_grid', 'smelt', 'check_name'}]


def divergence(left, right):
    a, b = normalized_actions(left), normalized_actions(right)
    for index in range(max(len(a), len(b))):
        if index >= len(a):
            return index, 'missing_action'
        if index >= len(b):
            return index, 'extra_action'
        if a[index] != b[index]:
            if a[index]['method'] != b[index]['method']:
                return index, 'action_type_or_order'
            if a[index]['params'] != b[index]['params']:
                return index, 'parameters'
            return index, 'result_or_cost'
    return None, 'identical'


def episode_key(row):
    return row['scope'], row['reading_mode'], row['condition'], row['world'], row['task_id']


def stale_adoption(record, conflicts):
    traces = record.get('policy_trace', record.get('result', {}).get('policy_trace', []))
    if traces and any('plan_mechanism_ids' not in trace for trace in traces):
        return None
    if not traces and record['method'] != 'pf_rag':
        return None
    ids = {m for t in traces for m in t.get('plan_mechanism_ids', [])}
    return int(any(m in conflicts and conflicts[m]['truth_vector'].get(record['world']) is False
                   and any(conflicts[m]['truth_vector'].values()) for m in ids))


def score(record, task, conflicts, nodes):
    terminal = outcome(record, record['events'], task)
    if terminal['SR'] != record['metrics']['SR']:
        raise RuntimeError('frozen outcome differs from episode success')
    stale = external_stale_map(record['events'], record['world'], conflicts, nodes)
    actions = classify_actions_v3(record['events'], stale_by_event=stale,
        conflicts=conflicts, resource_nodes=nodes, target=task['target']['item'],
        episode_success=bool(terminal['SR']))
    annotate_discriminative(actions, record['events'], record['world'], conflicts, nodes)
    selected = [a for a in actions if a['discriminative']]
    query = any(record['events'][a['event_index']]['method'] == 'check_name' for a in selected)
    action = any(record['events'][a['event_index']]['method'] != 'check_name' for a in selected)
    probes = [e for e in record['events'] if e.get('intent') == 'probe']
    return {
        'scope': record['scope'], 'reading_mode': record['reading_mode'],
        'method': record['method'], 'task_id': record['task_id'],
        'condition': record['condition'], 'world': record['world'], 'seed': record['seed'],
        'family_id': record['family_id'], 'true_feasible': terminal['true_feasible'],
        'SR': terminal['SR'], 'terminal': terminal['terminal'],
        'OC': terminal['false_abstention'] if terminal['true_feasible'] else None,
        'EF': terminal['terminal_execution_failure'],
        'HTR_all': int(any(stale.values())), 'stale_contact': int(bool(selected)),
        'query_contact': int(query), 'action_contact': int(action),
        'planning_adoption': stale_adoption(record, conflicts),
        'probe_count': len(probes), 'probe_cost': sum(e['cost']['primary'] for e in probes),
        'semantic_actions': terminal['semantic_actions'], 'semantic_cost': terminal['primary_cost'],
        'reader_replayed_search_count': record.get('reader', {}).get('search_count', 0),
        'runtime_search_calls': record.get('runtime_search_calls'), 'llm_calls': 0,
        'B': record.get('B'), 'no_conflict': record.get('no_conflict'),
        'evidence_sha256': record.get('evidence_sha256'),
    }


def baselines():
    results = {}
    files = [
        ('main', mode, ROOT / f'artifacts/main/{mode}/episodes.jsonl')
        for mode in ['all_public', 'llm_only']
    ] + [('main', 'oracle_reading', ROOT / 'artifacts/post_review/oracle_reading/episodes.jsonl'),
         ('ext_v2', 'all_public', WEB / 'reports/paperA/ext_v2/web_run/episodes.jsonl'),
         ('auto', '', WEB / 'reports/paperA/review_v14_zero_call/cp_rag/episodes.jsonl')]
    sources = []
    for scope, mode, path in files:
        if not path.exists():
            sources.append({'path': str(path), 'status': 'MISSING'})
            continue
        sources.append({'path': str(path), 'sha256': sha(path), 'status': 'PRESENT'})
        for r in jsonl(path):
            if r['method'] not in {'naive_rag', 'cp_rag'}:
                continue
            if scope == 'auto':
                if r['condition'] not in {c for c, _ in MAIN_CELLS}:
                    continue
                r['scope'] = 'ext_v2' if r['task_id'].startswith('extv2_') else 'main'
                r['reading_mode'] = r.get('reading_mode', 'all_public')
            else:
                r['scope'], r['reading_mode'] = scope, mode
            if r['scope'] == 'main' and r['method'] == 'naive_rag' and r['seed'] != 20261006:
                continue
            k = episode_key(r) + (r['method'],)
            if k in results:
                raise RuntimeError('ambiguous baseline duplicate')
            results[k] = r
    return results, sources


def compatibility(pf, baseline):
    reasons = []
    if pf['true_feasible'] != baseline['true_feasible']:
        reasons.append('gold_feasibility_mismatch')
    def inventory(record):
        return next((e['result'] for e in record['events'] if e['method'] == 'inventory'), None)
    if inventory(pf) != inventory(baseline):
        reasons.append('initial_inventory_mismatch')
    reader_a, reader_b = pf.get('reader', {}), baseline.get('reader', {})
    for field in ['search_count', 'unique_chunk_count', 'raw_claim_count']:
        if reader_a.get(field) != reader_b.get(field):
            reasons.append('initial_reader_' + field + '_mismatch')
    searches = reader_b.get('search_count', 0)
    researches = sum(t.get('decision') == 'research' for t in baseline.get('policy_trace', []))
    if searches + researches > 60:
        reasons.append('baseline_total_search_budget_exceeded')
    if sum(e['cost']['primary'] for e in baseline['events']) > 90:
        reasons.append('baseline_action_budget_exceeded')
    return reasons


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--input', type=Path, default=BASE)
    parser.add_argument('--output', type=Path, default=REPORT)
    args = parser.parse_args()
    verify_policy()
    status = json.loads((args.input / 'status.json').read_text())
    if status['status'] != 'COMPLETED':
        raise RuntimeError('analysis requires complete, deterministic formal execution')
    args.output.mkdir(parents=True, exist_ok=True)
    tasks = {r['id']: r for r in jsonl(WEB / 'data/tasks.jsonl')}
    tasks.update({r['id']: r for r in jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')})
    conflicts = {r['id']: r for r in load_conflicts(WEB / 'data/conflicts.jsonl')}
    nodes = load_resource_nodes(WEB / 'harness/config/arena.json')
    frozen = []
    for path in sorted((args.input / 'episodes').glob('*/*/episode.json')):
        frozen.append(json.loads(path.read_text()))
    if len(frozen) != 2364:
        raise RuntimeError('analysis matrix cardinality differs from registered 2364')
    peers, sources = baselines()
    scored, scored_peers, audit, census, divergences = [], {}, [], [], []
    no_conflict = []
    missing = []
    pf_lookup = {}
    for record in frozen:
        score_row = score(record, tasks[record['task_id']], conflicts, nodes)
        scored.append(score_row)
        for index, probe in enumerate(record['result']['opening_probes']):
            census.append({k: score_row[k] for k in ['scope', 'reading_mode', 'condition', 'world', 'task_id', 'seed']}
                          | {'probe_index': index, **probe})
        if record['seed'] != SEEDS[0]:
            continue
        k = episode_key(record)
        pf_lookup[k] = score_row
        for method in ['naive_rag', 'cp_rag']:
            peer = peers.get(k + (method,))
            if peer is None:
                missing.append({'key': list(k), 'method': method})
                continue
            peer['family_id'] = record['family_id']
            scored_peers[k + (method,)] = score(peer, tasks[peer['task_id']], conflicts, nodes)
            issues = compatibility(record, peer)
            audit.append({'scope': k[0], 'reading_mode': k[1], 'condition': k[2], 'world': k[3],
                          'task_id': k[4], 'comparator': method, 'compatible': not issues,
                          'issues': issues, 'same_frozen_initial_evidence_path': record['evidence_path'],
                          'historical_source_seed': peer['seed']})
            position, category = divergence(record, peer)
            divergences.append({'scope': k[0], 'reading_mode': k[1], 'condition': k[2], 'world': k[3],
                                'task_id': k[4], 'comparator': method,
                                'first_divergence_zero_based': position, 'category': category,
                                'action_count_delta': score_row['semantic_actions'] - scored_peers[k + (method,)]['semantic_actions'],
                                'compatible': not issues})
            if method == 'naive_rag' and record['no_conflict']:
                no_conflict.append({'key': list(k), 'identical': category == 'identical'})
    csv_out(args.output / 'per_episode.csv', scored)
    csv_out(args.output / 'baseline_per_episode.csv', list(scored_peers.values()))
    csv_out(args.output / 'probe_census.csv', census)
    csv_out(args.output / 'divergence_census.csv', divergences)
    csv_out(args.output / 'comparability_audit.csv', audit)
    contrasts, main_table = [], []
    for scope, mode in sorted({k[:2] for k in pf_lookup}):
        group = [v for k, v in pf_lookup.items() if k[:2] == (scope, mode)]
        for method in ['pf_rag', 'naive_rag', 'cp_rag']:
            data = group if method == 'pf_rag' else [v for k, v in scored_peers.items()
                if k[:2] == (scope, mode) and k[-1] == method]
            for metric in METRICS:
                values = [(r['task_id'] if scope == 'main' else r['family_id'], r[metric])
                          for r in data if r[metric] is not None]
                main_table.append({'scope': scope, 'reading_mode': mode, 'method': method,
                                   'metric': metric, **paired_ci(values)})
        for comparator in ['naive_rag', 'cp_rag']:
            compatible = all(r['compatible'] for r in audit
                if (r['scope'], r['reading_mode'], r['comparator']) == (scope, mode, comparator))
            pairs = [(r, scored_peers.get(episode_key(r) + (comparator,))) for r in group]
            complete = all(b is not None for _, b in pairs)
            for metric in METRICS:
                vals = [(a['task_id'] if scope == 'main' else a['family_id'], a[metric] - b[metric])
                        for a, b in pairs if b is not None and a[metric] is not None and b[metric] is not None]
                if not complete or not compatible:
                    result = {'estimate': None, 'ci_low': None, 'ci_high': None,
                              'n': len(vals), 'status': 'NOT_REPORTABLE_incomplete_or_incompatible_baseline'}
                elif metric != 'OC' and len(vals) != len(group):
                    result = {'estimate': None, 'ci_low': None, 'ci_high': None,
                              'n': len(vals), 'status': 'NOT_REPORTABLE_missing_metric_provenance'}
                else:
                    result = paired_ci(vals)
                contrasts.append({'scope': scope, 'reading_mode': mode,
                                  'contrast': 'pf_rag - ' + comparator, 'metric': metric, **result})
    csv_out(args.output / 'paired_contrasts.csv', contrasts)
    csv_out(args.output / 'main_table.csv', main_table)
    by_cell = []
    for scope, mode, method, condition, world in sorted({
            (r['scope'], r['reading_mode'], r['method'], r['condition'], r['world'])
            for r in scored if r['seed'] == SEEDS[0]}):
        rows = [r for r in scored if r['seed'] == SEEDS[0] and
                (r['scope'], r['reading_mode'], r['method'], r['condition'], r['world']) ==
                (scope, mode, method, condition, world)]
        for metric in METRICS:
            vals = [(r['task_id'] if scope == 'main' else r['family_id'], r[metric])
                    for r in rows if r[metric] is not None]
            by_cell.append({'scope': scope, 'reading_mode': mode, 'method': method,
                            'condition': condition, 'world': world, 'metric': metric, **paired_ci(vals)})
    csv_out(args.output / 'cell_table.csv', by_cell)
    cost = {'method': 'pf_rag', 'episodes': 2364, 'unique_instances': 1182,
            'elapsed_seconds': status['elapsed_seconds'], 'llm_calls': 0, 'currency_cost': 0,
            'semantic_cost_total': sum(r['semantic_cost'] for r in scored),
            'semantic_actions_total': sum(r['semantic_actions'] for r in scored),
            'probe_count_total': sum(r['probe_count'] for r in scored),
            'runtime_search_calls': 0, 'reader_replayed_searches': sum(r['reader_replayed_search_count'] for r in scored),
            'peak_worker_rss_kib': max(r['peak_rss_kib'] for r in frozen)}
    csv_out(args.output / 'cost_summary_addition.csv', [cost])
    policy = ROOT / 'src/arbiter/agent/pf_rag.py'
    tree = ast.parse(policy.read_text())
    forbidden_calls = [n.func.id for n in ast.walk(tree) if isinstance(n, ast.Call)
                       and isinstance(n.func, ast.Name) and n.func.id in {'open', 'eval', 'exec', '__import__'}]
    version_params = []
    for r in frozen:
        for e in r['events']:
            if re.search(r'(?<![\d.])1\.\d{1,2}(?:\.\d{1,2})?(?!\d|\.\d)', json.dumps(e.get('params', {}))):
                version_params.append([*episode_key(r), r['seed'], e['sequence']])
    qa = {'seed_determinism': json.loads((args.input / 'determinism.json').read_text()),
          'no_conflict_instances': len(no_conflict),
          'no_conflict_mismatches': [r for r in no_conflict if not r['identical']],
          'policy_forbidden_direct_calls': forbidden_calls, 'version_action_parameters': version_params,
          'missing_baselines': missing, 'baseline_source_hashes': sources,
          'new_llm_calls': 0, 'frozen_policy_verified': True,
          'runtime_boundary_basis': 'static policy audit plus public adapter capability; not OS syscall tracing'}
    dump(args.output / 'qa.json', qa)
    lines = ['# A22 PF-RAG formal results', '', 'Frozen B=1; fallback disabled; zero LLM calls.',
             'Two seeds are reproducibility replicas, not independent samples.',
             'Public original version mentions are retained under author clarification.', '',
             '## Primary SR contrasts', '']
    for r in contrasts:
        if r['metric'] == 'SR':
            lines.append(f"- {r['scope']} / {r['reading_mode']} / {r['contrast']}: "
                         f"{r.get('estimate')} [{r.get('ci_low')}, {r.get('ci_high')}], {r['status']}.")
    lines += ['', '## QA and limitations', '',
              f"- No-conflict instances: {len(no_conflict)}; mismatches: {len(qa['no_conflict_mismatches'])}.",
              f"- Missing baseline matches: {len(missing)}.",
              '- Full-plan adoption is not reconstructed from executed steps when old logs lack full plans; corresponding differences are NOT_REPORTABLE.',
              '- Search counts describe replayed frozen reader evidence. This run performed zero fresh retrieval/extraction calls.',
              '- Existing baseline records are retained; compatibility failures suppress paired claims rather than dropping unfavorable tasks.',
              '- Package replay validation is a separate pending QA step, not implied by seed agreement.']
    (args.output / 'REPORT.md').write_text('\n'.join(lines) + '\n')
    print(json.dumps({'episodes': len(scored), 'primary_contrasts': [r for r in contrasts if r['metric'] == 'SR'],
                      'no_conflict_mismatches': len(qa['no_conflict_mismatches'])}))


if __name__ == '__main__':
    main()
