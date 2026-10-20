#!/usr/bin/env python3
"""Offline formal-K-only export; never infer outcomes for unrun controls."""
from __future__ import annotations

import json
from collections import Counter

from arbiter.evaluation.external_rescore import TERMINALS
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes
from scripts import analyze_a21_mds as analysis
from scripts import run_a21_mds as run
from scripts.export_a21_development_closeout import ledger_usage


def main() -> int:
    jobs = run.material_rows('formal')
    records = [r for r in run.completed_records(run.stage_output('formal'), jobs) if r['condition'] == 'K']
    gate = run.success_gate(records, 32)
    if gate['feasible_n'] != 20:
        raise RuntimeError('formal_K_frozen_feasibility_scope_changed')
    if gate != json.loads((run.REPORT / 'formal_K_gate.json').read_text()):
        raise RuntimeError('formal_K_saved_gate_mismatch')
    conflicts = {r['id']: r for r in load_conflicts(run.WEB / 'data/conflicts.jsonl')}
    nodes = load_resource_nodes(run.WEB / 'harness/config/arena.json')
    rows, actions, sources = [], [], []
    for record in records:
        row, annotations = analysis.score(record, conflicts, nodes)
        rows.append(row)
        actions.extend(annotations)
        for field in ('action_log', 'prompt_log'):
            sources.append({'instance_id': record['instance_id'], 'kind': field,
                            'path': record[field], 'sha256': run.sha(run.ROOT / record[field])})
    out = run.REPORT / 'formal_K'
    analysis.write_csv(out / 'per_episode.csv', rows)
    analysis.write_csv(out / 'stale_action_annotations.csv', [
        {k: json.dumps(v, ensure_ascii=False, sort_keys=True) if isinstance(v, (dict, list)) else v
         for k, v in r.items()} for r in actions] or [{'instance_id': '', 'condition': 'K'}])
    table = {'condition': 'K', 'n': 32, 'feasible_n': 20, 'feasible_successes': sum(
        r['success'] for r in rows if r['true_feasible']), 'model': run.MODEL}
    for metric, feasible in (('success', False), ('feasible_success', True), ('stale_contact', False),
                             ('query_contact', False), ('action_contact', False), ('v3d_lower', False),
                             ('v3d_upper', False), ('overconservative', True), ('model_calls', False),
                             ('semantic_actions', False), ('primary_cost', False)):
        point, lo, hi, families = analysis.ci_by_family(rows, metric, feasible_only=feasible)
        table.update({metric: point, metric + '_ci_low': lo, metric + '_ci_high': hi,
                      metric + '_families': families})
    analysis.write_csv(out / 'main_table.csv', [table])
    counts = Counter(r['terminal'] for r in rows)
    analysis.write_csv(out / 'terminal_distribution.csv', [
        {'terminal': label, 'n': counts[label], 'denominator': 32, 'rate': counts[label] / 32}
        for label in TERMINALS])
    analysis.write_csv(out / 'source_hashes.csv', sources)
    run.write(out / 'gate.json', gate)
    run.write(out / 'cost.json', ledger_usage(run.stage_output('formal')))
    run.write(out / 'provenance.json', {
        'material_manifest_sha256': run.sha(run.material_manifest('formal')),
        'amendment_sha256': run.sha(run.GATE_AMENDMENT),
        'technical_entry_sha256': run.sha(run.REPORT / 'development_executability.json'),
        'bootstrap_seed': analysis.SEED, 'bootstrap_draws': analysis.DRAWS,
        'scope': '32 formal K only; controls not estimated', 'LLM_calls_for_export': 0})
    (out / 'README.md').write_text(
        '# Formal K only\n\n'
        'The numerical gate uses feasible successes / 20, not overall SR. '
        'All 32 K episodes are retained. Controls and contrasts are not estimated.\n\n'
        'Entry was corrected by author-approved amendment '
        '`prereg/a21_formal_gate_correction_20261019.md`; historical development gates are unchanged.\n\n'
        'Frozen limitations remain: flattened facts omit some executable recipe details; '
        'initial-only knowledge injection is not retained separately in subsequent requests; '
        'oracle cost validation alone does not establish all structural chain requirements. '
        'These results do not isolate a causal explanation of model performance.\n')
    print(json.dumps({'episodes': 32, 'gate': gate, 'report': str(out.relative_to(run.ROOT))}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
