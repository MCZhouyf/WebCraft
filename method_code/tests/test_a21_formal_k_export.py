"""Formal-K-only exports retain failed gates and do not fabricate controls."""
import csv
import json

from scripts import export_a21_formal_k as export


def test_failed_K_exports_all_32_without_controls(tmp_path, monkeypatch):
    run = export.run
    monkeypatch.setattr(run, 'ROOT', tmp_path)
    monkeypatch.setattr(run, 'REPORT', tmp_path / 'reports')
    records = [{'condition': 'K', 'instance_id': str(i), 'true_feasible': i < 20,
                'success': 0, 'action_log': 'actions', 'prompt_log': 'prompts'} for i in range(32)]
    gate = run.success_gate(records, 32)
    run.write(run.REPORT / 'formal_K_gate.json', gate)
    monkeypatch.setattr(run, 'material_rows', lambda name: [])
    monkeypatch.setattr(run, 'completed_records', lambda *args: records)
    monkeypatch.setattr(run, 'sha', lambda p: 'frozen')
    monkeypatch.setattr(export, 'load_conflicts', lambda p: [])
    monkeypatch.setattr(export, 'load_resource_nodes', lambda p: {})
    monkeypatch.setattr(export, 'ledger_usage', lambda p: {'successful_requests': 0})

    def score(row, conflicts, nodes):
        return {**row, 'family_id': str(int(row['instance_id']) % 7), 'terminal': 'budget_exhausted',
                **{k: 0 for k in ('feasible_success', 'stale_contact', 'query_contact', 'action_contact',
                                 'v3d_lower', 'v3d_upper', 'overconservative', 'model_calls',
                                 'semantic_actions', 'primary_cost')}}, []

    monkeypatch.setattr(export.analysis, 'score', score)
    assert export.main() == 0
    out = run.REPORT / 'formal_K'
    with (out / 'per_episode.csv').open() as handle:
        episodes = list(csv.DictReader(handle))
    assert len(episodes) == 32 and {r['condition'] for r in episodes} == {'K'}
    assert json.loads((out / 'gate.json').read_text()) == gate
    assert not (out / 'paired_contrasts.csv').exists()
