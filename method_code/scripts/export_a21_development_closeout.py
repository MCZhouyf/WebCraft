#!/usr/bin/env python3
"""Read-only, zero-call export of both A21 development rounds."""
from __future__ import annotations

import json
import platform
import subprocess
from collections import Counter
from importlib.metadata import version
from pathlib import Path
from statistics import mean
from typing import Any

from arbiter.evaluation.external_rescore import TERMINALS
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes
from scripts import analyze_a21_mds as analysis
from scripts import run_a21_mds as run

OUT = run.REPORT / 'development_closeout'


def failed_actions(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen: Counter = Counter()
    rows = []
    for index, event in enumerate(events):
        if event.get('method') not in ACTION_METHODS or event.get('success') is not False:
            continue
        params = json.dumps(event.get('params', {}), sort_keys=True)
        signature = event['method'], params
        result = event.get('result')
        if isinstance(result, dict) and result.get('error'):
            feedback = json.dumps(result['error'], ensure_ascii=False, sort_keys=True)
        elif event['method'] == 'craft_grid' and isinstance(result, dict) and result.get('product') is None:
            feedback = 'recipe_not_matched'
        else:
            feedback = 'other_public_failure'
        rows.append({'event_index0': index, 'sequence': event.get('sequence'),
                     'method': event['method'], 'params': params, 'feedback': feedback,
                     'public_result': json.dumps(result, ensure_ascii=False, sort_keys=True),
                     'additional_identical_failed_action': int(seen[signature] > 0)})
        seen[signature] += 1
    return rows


def ledger_usage(root: Path) -> dict[str, Any]:
    seen = set()
    inputs = outputs = cached = canaries = generations = duplicates = errors = 0
    by_scope: dict[str, Counter] = {}
    paths = sorted(root.rglob('*ledger.jsonl'))
    for path in paths:
        scope = next((stage for stage in ('development', 'development_short', 'formal')
                      if run.stage_output(stage) in path.parents), 'preflight_or_legacy_startup')
        counts = by_scope.setdefault(scope, Counter())
        for row in run.read_jsonl(path):
            if row.get('status') == 'infrastructure_error':
                errors += 1
                counts['infrastructure_error_records'] += 1
            if row.get('status') != 'ok':
                continue
            canonical = json.dumps(row, sort_keys=True, separators=(',', ':'))
            if canonical in seen:
                duplicates += 1
                continue
            seen.add(canonical)
            inc = int(row.get('input_tokens') or 0)
            out = int(row.get('output_tokens') or 0)
            cache = int(row.get('cached_input_tokens') or 0)
            inputs += inc
            outputs += out
            cached += cache
            counts['input_tokens'] += inc
            counts['output_tokens'] += out
            counts['cached_input_tokens'] += cache
            is_canary = bool(row.get('canary'))
            canaries += is_canary
            generations += not is_canary
            counts['canary_successes' if is_canary else 'task_generation_successes'] += 1
    return {'scope': 'all retained A21 HTTP ledgers, not accepted-episode logical usage',
            'ledger_files': len(paths), 'successful_requests': len(seen), 'canary_successes': canaries,
            'task_generation_successes': generations, 'duplicate_success_records_excluded': duplicates,
            'input_tokens': inputs, 'cached_input_tokens': cached, 'output_tokens': outputs,
            'infrastructure_error_records': errors,
            'estimated_CNY': (max(0, inputs-cached)*8+cached*2+outputs*28)/1_000_000,
            'rates_CNY_per_million': {'uncached_input': 8, 'cached_input': 2, 'output': 28},
            'cost_status': 'estimate, not an invoice', 'by_scope': {k: dict(v) for k, v in by_scope.items()},
            'limitations': ['Unledgered manual diagnostic calls are excluded.',
                            'Failed-request billing is unknown.',
                            'Saved-prefix replay does not add a new HTTP success record.']}


def table_row(stage: str, records: list[dict[str, Any]], rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {'stage': stage, 'scope': 'development_only', 'method': 'react', 'model': run.MODEL,
            'condition': 'K', 'n': len(rows), 'feasible_n': sum(r['true_feasible'] for r in rows),
            'successes': sum(r['success'] for r in rows), 'SR': mean(r['success'] for r in rows),
            'feasible_SR': mean(r['feasible_success'] for r in rows if r['true_feasible']),
            'mean_model_calls': mean(r['model_calls'] for r in rows),
            'total_logical_model_calls': sum(r['model_calls'] for r in rows),
            'mean_semantic_actions': mean(r['semantic_actions'] for r in rows),
            'mean_primary_cost': mean(r['primary_cost'] for r in rows),
            'episodes_with_any_failed_action': sum(r['gold_score']['any_action_failure'] for r in records),
            'call_cap_episodes': sum(r['gold_score']['call_cap_hit'] for r in records),
            **{metric: mean(r[metric] for r in rows) for metric in (
                'HTR_all', 'stale_contact', 'query_contact', 'action_contact', 'v3d_lower', 'v3d_upper')}}


def main() -> int:
    if (run.BASE / 'formal_run').exists():
        raise RuntimeError('closeout_scope_changed_formal_directory_exists')
    OUT.mkdir(parents=True, exist_ok=True)
    conflicts = {r['id']: r for r in load_conflicts(run.WEB/'data/conflicts.jsonl')}
    nodes = load_resource_nodes(run.WEB/'harness/config/arena.json')
    episodes, annotations, failures, table, terminals, sources, gates = [], [], [], [], [], [], []
    for stage in ('development', 'development_short'):
        records = run.completed_records(run.stage_output(stage), run.material_rows(stage))
        if len(records) != 8:
            raise RuntimeError('closeout_incomplete_development_scope')
        rows = []
        for record in records:
            row, actions = analysis.score(record, conflicts, nodes)
            events = run.read_jsonl(run.ROOT/record['action_log'])
            diagnostics = failed_actions(events)
            rows.append({**row, 'stage': stage,
                         'additional_identical_failed_actions': sum(
                             r['additional_identical_failed_action'] for r in diagnostics)})
            prefix = {'stage': stage, 'instance_id': record['instance_id'], 'task_id': record['task_id']}
            failures.extend({**prefix, **r} for r in diagnostics)
            annotations.extend({**r, 'stage': stage} for r in actions)
            for field in ('action_log', 'prompt_log'):
                path = run.ROOT/record[field]
                sources.append({**prefix, 'kind': field, 'path': record[field], 'sha256': run.sha(path)})
        episodes.extend(rows)
        table.append(table_row(stage, records, rows))
        counts = Counter(row['terminal'] for row in rows)
        terminals.extend({'stage': stage, 'terminal': label, 'n': counts[label],
                          'denominator': 8, 'rate': counts[label]/8} for label in TERMINALS)
        gate_path = run.REPORT/run.DEVELOPMENT_GATES[stage]
        gates.append({'stage': stage, 'path': str(gate_path.relative_to(run.ROOT)),
                      'sha256': run.sha(gate_path), 'result': json.loads(gate_path.read_text())})
    analysis.write_csv(OUT/'per_episode.csv', episodes)
    analysis.write_csv(OUT/'main_table.csv', table)
    analysis.write_csv(OUT/'terminal_distribution.csv', terminals)
    analysis.write_csv(OUT/'failed_actions.csv', failures)
    counts = Counter((r['stage'], r['method'], r['feedback']) for r in failures)
    analysis.write_csv(OUT/'failure_counts.csv', [
        {'stage': k[0], 'method': k[1], 'public_feedback': k[2], 'failed_action_count': value}
        for k, value in sorted(counts.items())])
    analysis.write_csv(OUT/'stale_action_annotations.csv', [
        {k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
         for k, v in row.items()} for row in annotations] or [{'stage': '', 'instance_id': ''}])
    analysis.write_csv(OUT/'source_hashes.csv', sources)
    analysis.write_csv(OUT/'formal_status.csv', [
        {'condition': condition, 'status': 'NOT_RUN', 'episodes': 0, 'SR': '',
         'reason': 'implemented_dev_gate_failed; formal gate placement correction awaits author decision'}
        for condition in ('K', 'K+S', 'K+N', 'empty')])
    usage = ledger_usage(run.BASE)
    analysis.write_csv(OUT/'ledger_sources.csv', [
        {'path': str(path.relative_to(run.ROOT)), 'sha256': run.sha(path),
         'records': len(run.read_jsonl(path))} for path in sorted(run.BASE.rglob('*ledger.jsonl'))])
    run.write(OUT/'cost.json', usage)
    run.write(OUT/'gate_records.json', {'gates': gates, 'modified': False,
              'protocol_placement_discrepancy': 'See a21_development_closeout_scope_note_20261019.md'})
    run.write(OUT/'environment.json', {
        'python': platform.python_version(), 'platform': platform.platform(),
        'packages': {name: version(name) for name in ('numpy', 'pulp', 'pytest')},
        'arbiter_commit_at_export': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=run.ROOT, text=True).strip(),
        'webcraft_commit_at_export': subprocess.check_output(
            ['git', 'rev-parse', 'HEAD'], cwd=run.WEB, text=True).strip(),
        'model_identifiers_in_completed_episodes': sorted({model for stage in ('development', 'development_short')
            for record in run.completed_records(run.stage_output(stage), run.material_rows(stage))
            for model in record['server_model_identifiers']}),
        'new_model_calls': 0, 'GPU_used': False,
    })
    run.write(OUT/'summary.json', {'scope': 'two separate development rounds, not a formal MDS result',
              'rounds': table, 'formal_episodes': 0, 'new_model_calls': 0,
              'new_simulator_episodes': 0, 'failure_events': len(failures),
              'cost_estimate_CNY': usage['estimated_CNY'], 'additional_identical_failed_actions': {
                  stage: sum(r['additional_identical_failed_action'] for r in failures if r['stage'] == stage)
                  for stage in ('development', 'development_short')}})
    print(json.dumps({'development_episodes_exported': len(episodes), 'formal_episodes': 0,
                      'new_model_calls': 0, 'round_summaries': table, 'cost': usage}, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
