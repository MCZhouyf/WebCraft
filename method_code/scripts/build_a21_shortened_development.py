#!/usr/bin/env python3
"""Build the one registered development-only terminal-chain retry offline."""
from __future__ import annotations

import copy
import fcntl
import json
import subprocess
import sys
import tempfile
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter
from scripts import run_a21_mds as run

OUT = run.BASE / 'development_short_materials'
PREREG = run.ROOT / 'prereg/a21_shortened_development_20261019.md'


def counts(rows: list[dict[str, Any]]) -> Counter:
    result = Counter()
    for row in rows:
        result[row['name']] += int(row['count'])
    return result


def mechanism(action: dict[str, Any]) -> tuple[str, str]:
    return action['method'], str(action.get('recipe_id') or action.get('node_type'))


def dependency(suffix: list[dict[str, Any]], initial: Counter) -> dict[str, Any]:
    if len(suffix) == 1:
        return {'kind': 'one_action_exception'}
    if len(suffix) != 2 or mechanism(suffix[0]) == mechanism(suffix[1]):
        raise RuntimeError('short_suffix_not_two_distinct_mechanisms')
    item = suffix[0]['expected']['name']
    last = suffix[1]
    consumed = Counter(value for value in last.get('layout', []) if value)
    if last['method'] == 'smelt':
        consumed[last['input']] += int(last['n'])
        consumed[last['fuel']] += int(last['fuel_count'])
    required = consumed[item]
    kind = 'consumed_input'
    if not required and last.get('requires_tool') == item:
        required, kind = 1, 'required_tool'
    if not required or initial[item] >= required:
        raise RuntimeError('short_suffix_missing_necessary_dependency')
    if initial[item] + int(suffix[0]['expected']['count']) < required:
        raise RuntimeError('short_suffix_producer_quantity_insufficient')
    return {'kind': kind, 'item': item, 'initial_count': initial[item], 'required_count': required}


def public_inventory(task: dict[str, Any], initial: Counter, additions: Counter) -> dict[str, Any]:
    result = copy.deepcopy(task)
    if not additions:
        return result
    # Publish full quantities for augmented items, not duplicate additive rows.
    result['standard_items'] = [{'name': key, 'count': value} for key, value in sorted(initial.items())
                                if value > 0 and not additions[key]]
    result['task_supplements'] = [{'name': key, 'count': initial[key]+value}
                                  for key, value in sorted(additions.items()) if value > 0]
    combined = result['standard_items'] + result['task_supplements']
    if len({row['name'] for row in combined}) != len(combined):
        raise RuntimeError('short_public_inventory_has_duplicates')
    if counts(combined) != initial + additions:
        raise RuntimeError('short_public_inventory_quantity_mismatch')
    return result


def apply_action(api: PublicSemanticAdapter, action: dict[str, Any]) -> None:
    method = action['method']
    if method == 'mine':
        result = api.mine(action['node_type'])
    elif method == 'craft_grid':
        result = api.craft_grid(action['layout'])
    elif method == 'smelt':
        result = api.smelt(action['input'], action['fuel'], int(action['n']))
    else:
        raise RuntimeError('unsupported_short_development_action')
    if result.get('success') is not True:
        raise RuntimeError('short_development_oracle_replay_failed')


def replay(sim: Any, world: str, initial: Counter, actions: list[dict[str, Any]], path: Path) -> Counter:
    handle = sim.create_simulated_agent(version=world, initial_inventory=initial, log_path=path)
    try:
        api = PublicSemanticAdapter(handle['api'])
        for action in actions:
            apply_action(api, action)
        return counts(api.inventory())
    finally:
        handle['shutdown']()


def main() -> int:
    if OUT.exists():
        raise RuntimeError('short_development_materials_already_exist')
    if not PREREG.is_file() or run.effective_available_bytes() < run.MIN_BYTES:
        raise RuntimeError('short_development_registration_or_memory_gate')
    gate = json.loads((run.REPORT / 'development_gate.json').read_text())
    if gate['passed'] or gate['feasible_success_rate'] >= gate['lower'] or gate['n'] != 8:
        raise RuntimeError('short_development_retry_not_eligible')
    frozen = 'f0c6e242398dfbc4f4544b665cb20ada1d1bad0a'
    checked = subprocess.run(['git', 'diff', '--quiet', frozen, '--', 'sim', 'truth', 'oracle',
                              'harness/config/arena.json', 'data/conflicts.jsonl'], cwd=run.WEB)
    if checked.returncode:
        raise RuntimeError('short_development_webcraft_core_drift')
    sys.path.insert(0, str(run.WEB))
    import sim
    from oracle.planner import OraclePlanner

    source_path = run.BASE / 'development_materials/manifest.json'
    source = json.loads(source_path.read_text())
    costs = {row['instance_id']: row for row in json.loads(
        (run.REPORT / 'development_oracle_costs.json').read_text())}
    if len(source['instances']) != 8:
        raise RuntimeError('short_development_source_scope_mismatch')
    with Path('/root/autodl-tmp/server.lock').open() as lock:
        fcntl.flock(lock, fcntl.LOCK_SH | fcntl.LOCK_NB)
        with tempfile.TemporaryDirectory(prefix='.short_development_', dir=run.BASE) as temporary:
            staging = Path(temporary) / 'materials'
            staging.mkdir()
            rows, audits = [], []
            for row in source['instances']:
                old_path = run.ROOT / row['visible_path']
                if run.sha(old_path) != row['visible_sha256']:
                    raise RuntimeError('short_development_source_hash_mismatch')
                old = json.loads(old_path.read_text())
                initial = counts(old['public_task']['standard_items'] + old['public_task'].get('task_supplements', []))
                target = old['public_task']['target']
                planner = OraclePlanner(old['target_world'], initial=initial, action_limit=90)
                original = planner.plan(target['item'], int(target['count']))
                if not original.feasible or original.primary_cost != costs[old['instance_id']]['oracle_primary_cost']:
                    raise RuntimeError('short_development_source_oracle_cost_mismatch')
                suffix = original.actions[-2:]
                prefix = original.actions[:-len(suffix)]
                identifier = 'dev2_' + old['source_task_id']
                validation = staging / 'validation'
                state = replay(sim, old['target_world'], initial, prefix, validation / (identifier+'__prefix.jsonl'))
                additions = state-initial
                short_initial = initial+additions
                if additions[target['item']] or short_initial[target['item']] >= int(target['count']):
                    raise RuntimeError('short_development_target_supplied_or_attained')
                edge = dependency(suffix, short_initial)
                task = public_inventory(old['public_task'], initial, additions)
                # K stays exactly as frozen, including redundant prefix facts.
                visible = {**old, 'instance_id': identifier, 'variant': 'single_shortened_development_retry',
                           'public_task': task}
                result = replay(sim, old['target_world'], short_initial, suffix,
                                validation / (identifier+'__suffix.jsonl'))
                if result[target['item']] < int(target['count']):
                    raise RuntimeError('short_development_suffix_did_not_attain_target')
                shortened = OraclePlanner(old['target_world'], initial=short_initial, action_limit=90).plan(
                    target['item'], int(target['count']))
                expected = min(2, len(original.actions))
                if not shortened.feasible or shortened.primary_cost != expected or len(shortened.actions) != expected:
                    raise RuntimeError('short_development_optimal_cost_or_action_count_mismatch')
                result = replay(sim, old['target_world'], short_initial, shortened.actions,
                                validation / (identifier+'__optimal.jsonl'))
                if result[target['item']] < int(target['count']):
                    raise RuntimeError('short_development_optimal_plan_did_not_attain_target')
                evidence = {'instance_id': identifier, 'source_instance_id': old['instance_id'],
                            'source_visible_sha256': row['visible_sha256'], 'true_feasible': True,
                            'source_oracle': original.as_dict(), 'omitted_prefix_actions': len(prefix),
                            'bypass_additions': dict(sorted(additions.items())), 'dependency': edge,
                            'retained_suffix': suffix, 'short_oracle': shortened.as_dict(),
                            'K_unchanged': old['conditions'] == visible['conditions'],
                            'public_replay_passed': True}
                visible_path = staging / (identifier+'.json')
                evaluator_path = staging / (identifier+'.evaluator.json')
                run.write(visible_path, visible)
                run.write(evaluator_path, evidence)
                rows.append({'instance_id': identifier, 'source_instance_id': old['instance_id'],
                             'task_id': old['public_task']['id'], 'target_world': old['target_world'],
                             'visible_path': str((OUT/visible_path.name).relative_to(run.ROOT)),
                             'visible_sha256': run.sha(visible_path),
                             'evaluator_path': str((OUT/evaluator_path.name).relative_to(run.ROOT)),
                             'evaluator_sha256': run.sha(evaluator_path),
                             'oracle_primary_cost': shortened.primary_cost,
                             'oracle_action_count': len(shortened.actions)})
                audits.append({key: evidence[key] for key in (
                    'instance_id', 'source_instance_id', 'source_visible_sha256', 'omitted_prefix_actions',
                    'bypass_additions', 'dependency', 'K_unchanged', 'public_replay_passed')})
            exceptions = sum(row['oracle_primary_cost'] == 1 for row in rows)
            if exceptions != 2 or len(rows) != 8:
                raise RuntimeError('short_development_registered_composition_mismatch')
            run.write(staging/'manifest.json', {'schema_version': 1, 'instances': rows,
                      'source_manifest_sha256': run.sha(source_path), 'prereg_sha256': run.sha(PREREG),
                      'webcraft_frozen_commit': frozen, 'conditions': ['K'], 'retry_number': 1})
            run.write(staging/'validation_report.json', {'new_model_calls': 0, 'passed': True,
                      'n': 8, 'two_step_instances': 6, 'one_step_exceptions': 2, 'instances': audits})
            staging.replace(OUT)
    print(json.dumps({'passed': True, 'n': 8, 'two_step_instances': 6,
                      'one_step_exceptions': 2, 'new_model_calls': 0}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
