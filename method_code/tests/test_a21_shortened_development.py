"""Synthetic shortening tests; source outcomes and held-out tasks are not read."""
from collections import Counter
import json

import pytest

from scripts import build_a21_shortened_development as build
from scripts import continue_a21_mds as controller
from scripts import run_a21_mds as runner


def test_public_inventory_is_disjoint_and_preserves_original_resources():
    task = {'target': {'item': 'goal', 'count': 1},
            'standard_items': [{'name': 'stick', 'count': 2}, {'name': 'stone', 'count': 2}],
            'task_supplements': []}
    initial = Counter(stick=2, stone=2)
    result = build.public_inventory(task, initial, Counter(stone=1, other=4))
    assert result['standard_items'] == [{'name': 'stick', 'count': 2}]
    assert result['task_supplements'] == [{'name': 'other', 'count': 4}, {'name': 'stone', 'count': 3}]
    rows = result['standard_items']+result['task_supplements']
    assert build.counts(rows) == Counter(stick=2, stone=3, other=4)
    assert len({r['name'] for r in rows}) == len(rows)
    assert task['task_supplements'] == []


def test_connected_suffix_requires_missing_intermediate():
    suffix = [
        {'method': 'craft_grid', 'recipe_id': 'intermediate', 'expected': {'name': 'x', 'count': 2}},
        {'method': 'craft_grid', 'recipe_id': 'goal', 'layout': ['x', 'x']+[None]*7},
    ]
    edge = build.dependency(suffix, Counter())
    assert edge == {'kind': 'consumed_input', 'item': 'x', 'initial_count': 0, 'required_count': 2}
    with pytest.raises(RuntimeError, match='necessary_dependency'):
        build.dependency(suffix, Counter(x=2))


def test_tool_edge_is_not_confused_with_consumption():
    suffix = [
        {'method': 'craft_grid', 'recipe_id': 'tool', 'expected': {'name': 'pick', 'count': 1}},
        {'method': 'mine', 'node_type': 'ore', 'requires_tool': 'pick'},
    ]
    assert build.dependency(suffix, Counter())['kind'] == 'required_tool'
    assert build.dependency(suffix[:1], Counter()) == {'kind': 'one_action_exception'}


def test_two_unrelated_actions_are_not_a_chain():
    suffix = [
        {'method': 'mine', 'node_type': 'rock', 'expected': {'name': 'x', 'count': 1}},
        {'method': 'craft_grid', 'recipe_id': 'goal', 'layout': ['y']+[None]*8},
    ]
    with pytest.raises(RuntimeError, match='necessary_dependency'):
        build.dependency(suffix, Counter())


def test_shortening_gate_is_single_and_cannot_bypass_high_gate(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'REPORT', tmp_path)
    first = tmp_path/'development_gate.json'
    second = tmp_path/'development_short_gate.json'
    first.write_text(json.dumps({'n': 8, 'passed': False, 'feasible_success_rate': .25, 'lower': .4}))
    assert runner.shortened_development_eligible()
    assert runner.eligible_development_gate() is None
    second.write_text(json.dumps({'passed': True, 'n': 8, 'feasible_n': 8, 'retry_number': 1,
                                 'material_manifest_sha256': runner.SHORTENED_MANIFEST_SHA256}))
    assert runner.eligible_development_gate() == second.name
    first.write_text(json.dumps({'n': 8, 'passed': False, 'feasible_success_rate': .875, 'lower': .4}))
    assert not runner.shortened_development_eligible()
    assert runner.eligible_development_gate() is None


def test_shortening_has_separate_materials_and_resume_scope(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'BASE', tmp_path)
    assert runner.stage_output('development_short') != runner.stage_output('development')
    assert runner.material_manifest('development_short') != runner.material_manifest('development')
    from scripts.a21_saved_prefix import select_prefix
    original = runner.stage_output('development')/'worker_1/prompts/react__K__old.jsonl'
    original.parent.mkdir(parents=True)
    original.write_text('not read: different stage')
    assert select_prefix(runner.stage_output('development_short'), 'react__K__old', tmp_path/'exclude') == (None, [], None)


def test_approved_correction_ignores_numeric_dev_gate_without_rerunning_dev(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'REPORT', tmp_path)
    monkeypatch.setattr(runner, 'BASE', tmp_path)
    monkeypatch.setattr(runner, 'shortened_development_eligible', lambda: True)
    source = runner.material_manifest('development_short')
    source.parent.mkdir()
    source.write_text('{}')
    value = {'passed': False, 'feasible_success_rate': .25, 'lower': .4, 'upper': .8}
    (tmp_path/'development_short_gate.json').write_text(json.dumps(value))
    reads, stages, events = [], [], []
    monkeypatch.setattr(runner, 'material_rows', lambda name: reads.append(name) or [])
    monkeypatch.setattr(runner, 'completed_records',
                        lambda path, jobs: [{}]*8 if path.name == 'development_run' else [])
    monkeypatch.setattr(runner, 'development_executability', lambda: {'passed': True})
    monkeypatch.setattr(controller, 'stage', lambda *args: stages.append(args) or False)
    monkeypatch.setattr(controller, 'publish', lambda status, **kw: events.append((status, kw)))
    controller.workflow()
    assert reads == ['development', 'formal']
    assert stages == [('formal',)]
    assert json.loads((tmp_path/'development_short_gate.json').read_text()) == value


def test_correction_does_not_consume_or_start_another_development_retry(tmp_path, monkeypatch):
    monkeypatch.setattr(runner, 'REPORT', tmp_path)
    monkeypatch.setattr(runner, 'BASE', tmp_path)
    monkeypatch.setattr(runner, 'shortened_development_eligible', lambda: True)
    source = runner.material_manifest('development_short')
    source.parent.mkdir()
    source.write_text('{}')
    monkeypatch.setattr(runner, 'material_rows', lambda name: [])
    monkeypatch.setattr(runner, 'completed_records',
                        lambda path, jobs: [{}]*8 if path.name == 'development_run' else [])
    monkeypatch.setattr(runner, 'development_executability', lambda: {'passed': True})
    stages = []

    def stage(name, phase='K'):
        stages.append((name, phase))
        if name == 'development_short':
            (tmp_path/'development_short_gate.json').write_text(json.dumps({'passed': True}))
            return True
        return False

    monkeypatch.setattr(controller, 'stage', stage)
    controller.workflow()
    assert stages == [('formal', 'K')]


def test_frozen_retry_materials_keep_all_eight_sources_and_K():
    jobs = runner.material_rows('development_short')
    originals = {row['instance_id']: row for row in runner.material_rows('development')}
    assert len(jobs) == len(originals) == 8
    for job in jobs:
        source = originals[job['source_instance_id']]
        old = json.loads((runner.ROOT/source['visible_path']).read_text())
        new = json.loads((runner.ROOT/job['visible_path']).read_text())
        assert new['public_task']['target'] == old['public_task']['target']
        assert new['target_world'] == old['target_world']
        assert new['source_task_id'] == old['source_task_id']
        assert new['conditions'] == old['conditions']
        assert job['visible_sha256'] == runner.sha(runner.ROOT/job['visible_path'])
        rows = new['public_task']['standard_items']+new['public_task']['task_supplements']
        assert len({r['name'] for r in rows}) == len(rows)
        before = build.counts(old['public_task']['standard_items']+old['public_task']['task_supplements'])
        after = build.counts(rows)
        assert all(after[key] >= value for key, value in before.items())
        assert after[new['public_task']['target']['item']] == 0


def test_retry_cost_composition_and_public_replay_are_frozen():
    rows = runner.material_rows('development_short')
    assert sorted(row['oracle_primary_cost'] for row in rows) == [1, 1, 2, 2, 2, 2, 2, 2]
    for row in rows:
        data = json.loads((runner.ROOT/row['evaluator_path']).read_text())
        assert data['public_replay_passed'] and data['K_unchanged']
        assert data['dependency']['kind'] in {'one_action_exception', 'consumed_input', 'required_tool'}


def test_modified_retry_manifest_stops_before_model_configuration(monkeypatch):
    real_sha = runner.sha
    monkeypatch.setattr(runner, 'sha', lambda path: 'changed' if path == runner.material_manifest('development_short')
                        else real_sha(path))
    with pytest.raises(RuntimeError, match='frozen_manifest_mismatch'):
        runner.material_rows('development_short')
