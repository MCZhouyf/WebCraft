"""Synthetic offline closeout accounting; no model or environment calls."""
import json

from scripts import export_a21_development_closeout as export


def test_failed_action_repetition_is_not_inferred_from_labels():
    events = [
        {'method': 'inventory', 'params': {}, 'success': False},
        {'method': 'craft_grid', 'params': {'layout': ['x']}, 'success': False,
         'result': {'product': None}},
        {'method': 'craft_grid', 'params': {'layout': ['x']}, 'success': False,
         'result': {'error': 'missing ingredient'}},
        {'method': 'craft_grid', 'params': {'layout': ['y']}, 'success': True},
        {'method': 'craft_grid', 'params': {'layout': ['y']}, 'success': False,
         'result': {'product': None}},
    ]
    rows = export.failed_actions(events)
    assert len(rows) == 3
    assert [r['additional_identical_failed_action'] for r in rows] == [0, 1, 0]
    assert rows[0]['feedback'] == 'recipe_not_matched'
    assert 'missing ingredient' in rows[1]['feedback']


def test_ledger_accounting_deduplicates_only_identical_success_records(tmp_path, monkeypatch):
    monkeypatch.setattr(export.run, 'BASE', tmp_path)
    row = {'status': 'ok', 'call_type': 'external_react', 'timestamp': 'one',
           'input_tokens': 100, 'cached_input_tokens': 20, 'output_tokens': 10, 'canary': False}
    a = tmp_path/'development_run/worker_1/ledger.jsonl'
    b = tmp_path/'development_short_run/worker_1/ledger.jsonl'
    for path in (a, b):
        path.parent.mkdir(parents=True)
        path.write_text(json.dumps(row)+'\n')
    with b.open('a') as handle:
        handle.write(json.dumps({'status': 'infrastructure_error', 'http_status': 429})+'\n')
        handle.write(json.dumps({'status': 'WAITING_HTTP_429'})+'\n')
    result = export.ledger_usage(tmp_path)
    assert result['successful_requests'] == 1
    assert result['duplicate_success_records_excluded'] == 1
    assert result['task_generation_successes'] == 1
    assert result['infrastructure_error_records'] == 1
    assert result['estimated_CNY'] == .00096
