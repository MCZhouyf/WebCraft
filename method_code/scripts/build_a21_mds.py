#!/usr/bin/env python3
"""Build and oracle-verify the post-review A21 medium-depth suite offline."""
from __future__ import annotations

import hashlib
import json
import shutil
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
WEB = Path('/root/autodl-tmp/webcraft')
OUT = ROOT / 'artifacts/post_review/a21_mds_20261018/materials'
REPORT = ROOT / 'reports/main/post_review/a21_mds_20261018'

# Eight held-out EXT-v2 mechanism families.  Four frozen directed pairs each
# provide 32 MDS instances; the evaluator records intentionally infeasible
# target worlds separately rather than inventing a route for them.
SELECTED = {
    'extv2_001': {'bypass': {}, 'label': 'blue_carpet'},
    'extv2_003': {'bypass': {}, 'label': 'iron_ingot'},
    'extv2_007': {'bypass': {}, 'label': 'chiseled_bookshelf'},
    'extv2_009': {'bypass': {}, 'label': 'lodestone'},
    # One explicitly disclosed public material shortcut changes 11 -> 10.
    'extv2_010': {'bypass': {'oak_log': 1}, 'label': 'oak_chest_boat'},
    'extv2_011': {'bypass': {}, 'label': 'saddle'},
    'extv2_012': {'bypass': {}, 'label': 'chain'},
    'extv2_013': {'bypass': {}, 'label': 'iron_chain'},
}
CONDITIONS = ('K', 'K+S', 'K+N', 'empty')


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + '\n')


def action_fact(action: dict[str, Any]) -> dict[str, str]:
    expected = action.get('expected', {})
    output = str(expected.get('name', 'unknown'))
    method = action.get('method')
    if method == 'mine':
        text = f"{action['node_type']} drops {output}."
    elif method == 'smelt':
        text = f"{output} can be smelted from {action['input']} using {action['fuel']}."
    elif method == 'craft_grid':
        parts = [str(value) for value in action['layout'] if value]
        text = f"{output} can be made from {', '.join(parts)} at crafting_grid."
    else:
        raise ValueError(f'unsupported oracle action: {method}')
    return {'mechanism fact': text, 'source': 'truth_oracle_path', 'timestamp': '2026-10-18T00:00:00Z'}


def main() -> int:
    if OUT.exists():
        raise RuntimeError('A21 materials already exist; refusing regeneration')
    sys.path.insert(0, str(WEB))
    from oracle.planner import INITIAL, OraclePlanner  # evaluator-only import

    ext_tasks = {row['id']: row for row in jsonl(WEB / 'data/ext_v2/ext_v2_tasks_20261016.jsonl')}
    source_manifest = json.loads((WEB / 'cds_materials/cds_manifest.json').read_text())['rows']
    pairs = json.loads((WEB / 'config/ext_v2/mechanism_families.json').read_text())['directed_comparison_pairs']
    source_by_key = {(row['task_id'], row['target_world'], row['comparison_world']): row for row in source_manifest}
    selected_pairs = [row for row in pairs if row['task_id'] in SELECTED]
    if len(selected_pairs) != 32:
        raise RuntimeError(f'expected 32 registered directed pairs, got {len(selected_pairs)}')

    rows, oracle_rows = [], []
    OUT.mkdir(parents=True)
    for ordinal, pair in enumerate(selected_pairs, 1):
        task = ext_tasks[pair['task_id']]
        source = source_by_key[(pair['task_id'], pair['target_world'], pair['comparison_world'])]
        source_material = json.loads((WEB / source['visible_path']).read_text())
        bypass = Counter(SELECTED[pair['task_id']]['bypass'])
        initial = Counter(INITIAL) + bypass
        planner = OraclePlanner(pair['target_world'], initial=initial, action_limit=10)
        plan = planner.plan(task['target']['item'], int(task['target']['count']))
        feasible = bool(plan.feasible)
        # Feasible directional cells must be genuinely medium depth.  Infeasible
        # cells intentionally have no oracle route and are reported as such.
        if feasible and not 4 <= int(plan.primary_cost) <= 10:
            raise RuntimeError(f'outside MDS oracle range: {pair} -> {plan.primary_cost}')
        path_rows = [action_fact(action) for action in plan.actions] if feasible else []
        conditions: dict[str, list[dict[str, str]]] = {}
        for condition in CONDITIONS:
            visible = [dict(value) for value in source_material['conditions'][condition]]
            if condition != 'empty':
                # Every nonempty MDS condition includes its target-world route
                # facts. Existing CDS facts are frozen truth-derived facts.
                visible.extend(path_rows)
            conditions[condition] = visible
        instance_id = f"mds_{ordinal:03d}_{pair['task_id']}_{pair['target_world'].replace('.', '_')}_vs_{pair['comparison_world'].replace('.', '_')}"
        public_task = {
            'id': f'mds_{ordinal:03d}_{pair["task_id"]}',
            'target': dict(task['target']),
            'standard_items': [{'name': key, 'count': value} for key, value in sorted(initial.items())],
            'task_supplements': [],
        }
        material = {
            'schema_version': 1, 'instance_id': instance_id,
            'source_task_id': pair['task_id'], 'family_id': task['independent_family_id'],
            'target_world': pair['target_world'], 'comparison_world': pair['comparison_world'],
            'public_task': public_task, 'conditions': conditions,
        }
        evaluator = {
            'instance_id': instance_id, 'target_world': pair['target_world'],
            'comparison_world': pair['comparison_world'], 'true_feasible': feasible,
            'oracle_primary_cost': int(plan.primary_cost) if feasible else None,
            'oracle_action_count': len(plan.actions) if feasible else None,
            'oracle_actions': plan.actions if feasible else [],
            'bypass_materials': dict(sorted(bypass.items())),
            'source_cds_material': source['visible_path'],
        }
        visible_path = OUT / f'{instance_id}.json'
        evaluator_path = OUT / f'{instance_id}.evaluator.json'
        write_json(visible_path, material)
        write_json(evaluator_path, evaluator)
        rows.append({'instance_id': instance_id, 'source_task_id': pair['task_id'],
                     'family_id': task['independent_family_id'], 'target_world': pair['target_world'],
                     'comparison_world': pair['comparison_world'], 'visible_path': str(visible_path.relative_to(ROOT)),
                     'evaluator_path': str(evaluator_path.relative_to(ROOT)),
                     'visible_sha256': sha(visible_path), 'evaluator_sha256': sha(evaluator_path)})
        oracle_rows.append({**evaluator, 'instance_id': instance_id, 'source_task_id': pair['task_id'],
                            'family_id': task['independent_family_id']})
    write_json(OUT / 'manifest.json', {'schema_version': 1, 'instances': rows, 'conditions': list(CONDITIONS),
                                       'source_manifest_sha256': sha(WEB / 'cds_materials/cds_manifest.json'),
                                       'source_pairs_sha256': sha(WEB / 'config/ext_v2/mechanism_families.json')})
    write_json(REPORT / 'oracle_costs.json', oracle_rows)
    # Development viability uses exactly the eight pre-existing development
    # task mechanisms.  It is generated independently and never shares an
    # MDS held-out material file or instance identifier.
    dev_rows = []
    dev_dir = ROOT / 'artifacts/post_review/a21_mds_20261018/development_materials'
    dev_tasks = [row for row in jsonl(WEB / 'data/tasks.jsonl') if row.get('split') == 'dev']
    if len(dev_tasks) != 8:
        raise RuntimeError(f'expected exactly 8 development tasks, got {len(dev_tasks)}')
    for task in dev_tasks:
        viable = [(world, plan) for world, plan in task['oracle_plans'].items() if plan]
        if not viable:
            raise RuntimeError(f'development task has no viable world: {task["id"]}')
        # Pick the lowest-cost viable registered oracle route deterministically.
        world, oracle = min(viable, key=lambda pair: (pair[1]['primary_cost'], pair[0]))
        facts = [action_fact(action) for action in oracle['actions']]
        material = {
            'schema_version': 1, 'instance_id': f'dev_{task["id"]}', 'source_task_id': task['id'],
            'family_id': f'dev:{task["type"]}', 'target_world': world,
            'public_task': {'id': task['id'], 'target': dict(task['target']),
                            'standard_items': task['standard_items'],
                            'task_supplements': task.get('task_supplements', [])},
            'conditions': {'K': facts},
        }
        visible = dev_dir / f'dev_{task["id"]}.json'
        write_json(visible, material)
        dev_rows.append({'instance_id': material['instance_id'], 'task_id': task['id'], 'target_world': world,
                         'oracle_primary_cost': oracle['primary_cost'], 'oracle_action_count': len(oracle['actions']),
                         'visible_path': str(visible.relative_to(ROOT)), 'visible_sha256': sha(visible)})
    write_json(dev_dir / 'manifest.json', {'schema_version': 1, 'instances': dev_rows,
                                           'source_tasks_sha256': sha(WEB / 'data/tasks.jsonl')})
    write_json(REPORT / 'development_oracle_costs.json', dev_rows)
    print(json.dumps({'instances': len(rows), 'feasible': sum(x['true_feasible'] for x in oracle_rows),
                      'infeasible': sum(not x['true_feasible'] for x in oracle_rows),
                      'oracle_costs': sorted({x['oracle_primary_cost'] for x in oracle_rows if x['true_feasible']}),
                      'development_instances': len(dev_rows)}))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
