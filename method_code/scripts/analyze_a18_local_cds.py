"""Separate A18 backend/GLM comparisons using the unchanged A8 scorers."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path

from scripts import analyze_cds_agents_20261017 as a8
from scripts.a18_local_backend import IDENTITY
from scripts.run_a18_local_cds import OUT, REPORT, ROOT, WEB

CONDITIONS = ('K', 'K+N', 'K+S', 'empty')


def read_csv(path):
    with path.open(encoding='utf-8-sig', newline='') as handle:
        return list(csv.DictReader(handle))


def write_csv(report, name, rows, fields):
    with (report / name).open('w', newline='', encoding='utf-8-sig') as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator='\n')
        writer.writeheader()
        writer.writerows(rows)


def direction(left, right):
    if left == '' or right == '':
        return 'NOT_ESTIMABLE'
    if left == right == 0:
        return 'BOTH_ZERO'
    if left == 0 or right == 0:
        return 'ONE_ZERO'
    return 'SAME_DIRECTION' if (left > 0) == (right > 0) else 'OPPOSITE_DIRECTION'


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--allow-partial', action='store_true')
    parser.add_argument('--backend', choices=('local_nf4', 'remote_service'), default='local_nf4')
    parser.add_argument('--include-recovery', action='store_true')
    args = parser.parse_args()
    remote = args.backend == 'remote_service'
    out, report = OUT.parent / args.backend, REPORT.parent / args.backend
    if not (out / 'run_manifest.json').exists():
        raise RuntimeError('A18_backend_not_started')
    run_manifest = json.loads((out / 'run_manifest.json').read_text())
    identity = run_manifest['required_server_model_identifier'] if remote else IDENTITY
    label = 'Qwen_remote_service' if remote else 'Qwen3-14B_NF4'
    report.mkdir(parents=True, exist_ok=True)
    records = a8.jsonl(out / 'episodes.jsonl') if (out / 'episodes.jsonl').exists() else []
    roots = [out]
    if args.include_recovery:
        if not remote:
            raise RuntimeError('recovery_is_remote_only')
        recovery = out.parent / 'remote_service_resume1'
        recovery_manifest = json.loads((recovery / 'run_manifest.json').read_text())
        import hashlib
        if (recovery_manifest['parent_completed_sha256'] != hashlib.sha256((out / 'episodes.jsonl').read_bytes()).hexdigest()
                or recovery_manifest['required_server_model_identifier'] != identity):
            raise RuntimeError('recovery_parent_or_identity_changed')
        roots.append(recovery)
        records.extend(a8.jsonl(recovery / 'episodes.jsonl') if (recovery / 'episodes.jsonl').exists() else [])
    if len(records) != 208 and not args.allow_partial:
        raise RuntimeError('A18_incomplete_use_explicit_partial_analysis')
    keys = [(r['condition'], r['instance_id']) for r in records]
    if len(keys) != len(set(keys)):
        raise RuntimeError('A18_duplicate_episode_keys')
    manifest = {r['instance_id']: r for r in json.loads((WEB / 'cds_materials/cds_manifest.json').read_text())['rows']}
    tasks = {r['id']: r for r in a8.jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    conflicts = {r['id']: r for r in a8.load_conflicts(WEB / 'data/conflicts.jsonl')}
    nodes = a8.load_resource_nodes(WEB / 'harness/config/arena.json')
    frames, actions = [], []
    for record in records:
        expected_local = [] if remote else [identity]
        expected_server = [identity] if remote else []
        if (record['local_model_identifiers'] != expected_local
                or record['server_model_identifiers'] != expected_server):
            raise RuntimeError('A18_model_identity_or_server_provenance_invalid')
        row, annotations = a8.audit_episode(record, conflicts, nodes, manifest, tasks)
        if not remote:
            row['local_model'] = identity
            row.pop('server_model')
        frames.append(row)
        actions.extend(annotations)
    numeric = ('success', 'true_feasible', 'discriminative_stale_contact', 'query_contact',
               'action_contact', 'v3d_lower', 'v3d_upper', 'action_model_calls', 'boundary_model_calls')
    glm = [{**r, **{k: int(r[k]) for k in numeric}} for r in
           read_csv(ROOT / 'reports/main/post_review/a18_qwen_cds_20261017/glm_reference_episodes.csv')]
    metrics = ('success', 'feasible_SR', 'discriminative_stale_contact', 'query_contact',
               'action_contact', 'v3d_lower', 'v3d_upper')
    conditions = []
    for model, data in ((label, frames), ('GLM-5.2_A8', glm)):
        for condition in CONDITIONS:
            subset = [r for r in data if r['condition'] == condition]
            for metric in metrics:
                eligible = (lambda r: bool(r['true_feasible'])) if metric == 'feasible_SR' else (lambda r: True)
                column = 'success' if metric == 'feasible_SR' else metric
                selected = [r for r in subset if eligible(r)]
                families = len({r['family_id'] for r in selected})
                point, low, high = '', '', ''
                if selected:
                    point, low, high, _ = a8.bootstrap_mean(subset, column, eligible)
                    if families < 2:
                        low = high = ''
                conditions.append({'model': model, 'condition': condition, 'metric': metric,
                                   'n_observed': len(subset), 'n_eligible': len(selected), 'families': families,
                                   'estimate': point, 'ci_low': low, 'ci_high': high})
    paired, comparisons = [], []
    for left, right, chosen in (('K+S', 'K+N', ('success', 'discriminative_stale_contact', 'v3d_lower', 'v3d_upper')),
                                ('empty', 'K', ('success', 'feasible_SR'))):
        common = ({r['instance_id'] for r in frames if r['condition'] == left}
                  & {r['instance_id'] for r in frames if r['condition'] == right})
        for metric in chosen:
            points = {}
            for model, data in ((label, frames), ('GLM-5.2_A8', glm)):
                la = [r for r in data if r['condition'] == left and r['instance_id'] in common]
                rb = [r for r in data if r['condition'] == right and r['instance_id'] in common]
                eligible = (lambda r: bool(r['true_feasible'])) if metric == 'feasible_SR' else (lambda r: True)
                column = 'success' if metric == 'feasible_SR' else metric
                selected = [r for r in la if eligible(r)]
                families = len({r['family_id'] for r in selected})
                point, low, high, n = '', '', '', 0
                if selected:
                    point, low, high, n = a8.bootstrap_difference(la, rb, column, eligible)
                    if families < 2:
                        low = high = ''
                paired.append({'model': model, 'contrast': f'{left} - {right}', 'metric': metric,
                               'point': point, 'ci_low': low, 'ci_high': high,
                               'paired_n': n, 'families': families, 'seed': 20261017, 'draws': 10000})
                points[model] = point
            comparisons.append({'contrast': f'{left} - {right}', 'metric': metric,
                                label: points[label], 'GLM_5_2': points['GLM-5.2_A8'],
                                'direction': direction(points[label], points['GLM-5.2_A8'])})
    write_csv(report, 'episode_metrics.csv', frames, list(frames[0]) if frames else ['method', 'condition', 'instance_id'])
    action_fields = list(dict.fromkeys(key for row in actions for key in row))
    write_csv(report, 'stale_actions.csv', actions, action_fields or ['instance_id', 'condition', 'event_index'])
    write_csv(report, 'condition_metrics.csv', conditions, list(conditions[0]))
    wide = []
    for model in (label, 'GLM-5.2_A8'):
        for condition in CONDITIONS:
            subset = [r for r in conditions if r['model'] == model and r['condition'] == condition]
            item = {'model': model, 'condition': condition, 'n': subset[0]['n_observed']}
            for row in subset:
                for suffix, key in (('', 'estimate'), ('_ci_low', 'ci_low'), ('_ci_high', 'ci_high')):
                    item[row['metric'] + suffix] = row[key]
                if row['metric'] == 'feasible_SR':
                    item['feasible_n'] = row['n_eligible']
            wide.append(item)
    write_csv(report, 'main_table.csv', wide, list(wide[0]))
    write_csv(report, 'paired_contrasts.csv', paired, list(paired[0]))
    write_csv(report, 'direction_comparison.csv', comparisons, list(comparisons[0]))
    ledger = [row for directory in roots for row in
              (a8.jsonl(directory / 'ledger.jsonl') if (directory / 'ledger.jsonl').exists() else [])]
    successful = [r for r in ledger if r.get('record_type') == 'generation' and r.get('status') == 'ok']
    status = json.loads((roots[-1] / 'status.json').read_text()) if (roots[-1] / 'status.json').exists() else {'status': 'RUNNING'}
    if args.include_recovery:
        combined = {'status': status['status'], 'completed': len(records), 'planned': 208,
                    'segments': [str(directory.relative_to(ROOT)) for directory in roots],
                    'replayed_responses_not_counted_as_new_generations': sum(r.get('replayed_saved_responses', 0) for r in records)}
        (report / 'combined_status.json').write_text(json.dumps(combined, indent=2, sort_keys=True) + '\n')
    attempts = len(successful) + sum(r.get('record_type') == 'infrastructure_error' for r in ledger)
    usage = {'episodes_completed': len(records), 'planned': 208,
             'local_generations': 0 if remote else len(successful),
             'remote_generations': len(successful) if remote else 0,
             'task_generations': sum(not r.get('canary') for r in successful),
             'canary_generations': sum(bool(r.get('canary')) for r in successful),
             'input_tokens': sum(r['usage']['prompt_tokens'] for r in successful),
             'output_tokens': sum(r['usage']['completion_tokens'] for r in successful),
             'API_calls': attempts if remote else 0,
             'provider_charge': 'UNREPORTED' if remote else 'NOT_APPLICABLE', 'hardware_currency_cost': None,
             'local_model_identifier': None if remote else identity,
             'server_model_identifier': identity if remote else None,
             'condition_coverage': dict(Counter(r['condition'] for r in records)),
             'runtime_status': status['status']}
    (report / 'usage.json').write_text(json.dumps(usage, indent=2, sort_keys=True) + '\n')
    text = [f'# A18 {label} CDS replication', '',
            f'Status: {status["status"]}; completed {len(records)}/208. Identity: `{identity}`.',
            ('Existing remote service, independent parallel episodes; local 14B records are not pooled.' if remote else
             'One local quantized model, sequential decoding, no inference API or server.'),
            ('Weight size/precision and physical GPU topology are not established by a response alias.' if remote else
             'This is NF4 double-quantized BF16 inference, not an unquantized model-equivalence claim.'),
            'Materials/messages, first-observation injection defect, bounded history and A8 scorer are unchanged.',
            'No repaired BIA claim. A20 corrections are not silently substituted into either primary model score.',
            'GLM/Qwen are not pooled. Partial contrasts restrict both models to identical observed pairs.', '',
            '| Contrast | Metric | Qwen point | GLM point | Direction |', '|---|---|---:|---:|---|']
    for row in comparisons:
        text.append(f'| {row["contrast"]} | {row["metric"]} | {row[label]} | {row["GLM_5_2"]} | {row["direction"]} |')
    text += ['', 'Full estimates and family-cluster intervals: condition_metrics.csv and paired_contrasts.csv.',
             'Bootstrap: 10,000 draws, seed 20261017, unchanged A8 estimators. Missing pairs are never cognitive failures.',
             'Generation counts, inference API attempts, and actual token usage are separated in usage.json.',
             'Original frozen methods/results and WebCraft are unchanged. No remote push.', '']
    if args.include_recovery:
        text += ['The first remote segment stopped on one request timeout after 182 complete keys.',
                 'Only its 26 missing keys were continued. Four saved prefixes (62 replies) were byte-checked',
                 'and restored offline; these are not additional model generations or repeated completed episodes.',
                 'Client timeout became 900 seconds; method, model, prompt and episode budgets stayed fixed.',
                 'Original stop status remains preserved; combined_status.json describes the combined coverage.', '']
    (report / 'REPORT.md').write_text('\n'.join(text))
    print(json.dumps(usage, sort_keys=True))


if __name__ == '__main__':
    main()
