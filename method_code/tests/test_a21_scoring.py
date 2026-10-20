"""Synthetic transport-to-scorer fixtures; no model calls or task data."""
import json

import pytest

from scripts.analyze_a21_mds import ci_by_family, paired_ci, score


@pytest.mark.parametrize('initial_old_widget,expected_propagation', [(1, 0), (0, 1)])
def test_A21_uses_frozen_FIFO_or_absent_reference_rule(tmp_path, initial_old_widget, expected_propagation):
    events = [
        {'method': 'inventory', 'result': [{'name': 'old_widget', 'count': initial_old_widget}],
         'params': {}, 'success': True},
        {'method': 'craft_grid', 'params': {'layout': ['stick'] + [None] * 8}, 'success': False,
         'result': {'error': 'invalid recipe'}, 'item_flow': {'produced': [], 'consumed': []}},
        {'method': 'smelt', 'params': {'input': 'old_widget', 'fuel': 'coal'}, 'success': False,
         'result': {}, 'item_flow': {'produced': [], 'consumed': []}},
        {'method': 'submit', 'params': {'result': {'feasible': False}}, 'success': True},
    ]
    path = tmp_path / 'actions.jsonl'
    path.write_text(''.join(json.dumps(row) + '\n' for row in events))
    record = {'action_log': str(path), 'prompt_log': 'unused', 'target_world': 'new',
              'target_item': 'goal', 'success': 0, 'condition': 'K+S', 'instance_id': 'synthetic',
              'task_id': 'synthetic', 'source_task_id': 'synthetic', 'family_id': 'synthetic',
              'comparison_world': 'old', 'seed': 20261018, 'true_feasible': True,
              'reported_feasible': False, 'metrics': {'HTR_all': 1}, 'model_calls': 2,
              'input_tokens': 0, 'output_tokens': 0, 'primary_cost': 2, 'model_output_error': None,
              'gold_score': {'terminal': 'wrong_infeasible', 'semantic_actions': 2}}
    conflicts = {'old_recipe': {'id': 'old_recipe', 'assertion': {
        'kind': 'recipe', 'station': 'crafting_grid', 'output': 'old_widget',
        'ingredients': [['stick']], 'ordered': False}, 'truth_vector': {'new': False, 'old': True}}}
    result, annotations = score(record, conflicts, {})
    assert result['v3d_lower'] == expected_propagation
    assert annotations[0]['v3_label'] == 'commit'


def test_feasible_success_point_uses_feasible_instances_with_family_resampling():
    rows = [{'instance_id': str(i), 'family_id': 'a' if i < 4 else 'b',
             'true_feasible': True, 'success': int(i < 4)} for i in range(5)]
    assert ci_by_family(rows, 'success', feasible_only=True)[0] == 0.8
    baseline = [{**row, 'success': 0} for row in rows]
    assert paired_ci(rows, baseline, 'success', feasible_only=True)[0] == 0.8
