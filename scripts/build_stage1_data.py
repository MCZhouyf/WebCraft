#!/usr/bin/env python3
"""Finalize conflict grades, resource capacities, and the deterministic task corpus."""
from __future__ import annotations

import json
import math
import random
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

ROOT = Path('/root/autodl-tmp/webcraft')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from conflict_core import assertion_id, recipe_assertion  # noqa: E402
from recipe_type_policy import rejection_reason  # noqa: E402
from oracle.planner import (INITIAL, SOURCE_SPECS, VERSIONS, OraclePlanner,
                            item_id, validate_plan)  # noqa: E402


def compact(value):
    return json.dumps(value, sort_keys=True, separators=(',', ':'))


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text().splitlines() if line]


class Builder:
    def __init__(self):
        self.draft = read_jsonl(ROOT / 'data/drafts/conflicts.jsonl')
        self.truth = {version: json.loads((ROOT / f'truth/{version}.json').read_text())
                      for version in VERSIONS}
        self.common_names = set.intersection(*(
            set(map(item_id, self.truth[version]['names']['items'])) for version in VERSIONS))
        self.unlimited = {version: OraclePlanner(version) for version in VERSIONS}
        self.bundle_cache = {}
        self.target_cache = {}
        self.raw_recipe = {
            version: {recipe['id']: recipe for recipe in self.truth[version]['recipes']}
            for version in VERSIONS
        }
        self.conflict_ids = {row['id'] for row in self.draft}
        self.row_by_id = {row['id']: row for row in self.draft}

    def bundle_plan(self, planner, requirements):
        key = (planner.version, tuple(sorted(requirements.items())),
               tuple(sorted(planner.capacities.items())))
        if key not in self.bundle_cache:
            result = planner.plan_requirements(dict(requirements), label='probe_bundle')
            if result.feasible:
                validate_plan(result)
            self.bundle_cache[key] = result
        return self.bundle_cache[key]

    def target_plan(self, planner, target):
        key = (planner.version, target, tuple(sorted(planner.capacities.items())))
        if key not in self.target_cache:
            result = planner.plan(target)
            if result.feasible:
                validate_plan(result)
            self.target_cache[key] = result
        return self.target_cache[key]

    def choice_score(self, item, planners):
        results = [self.bundle_plan(planners[v], Counter({item: 1})) for v in VERSIONS]
        if not all(result.feasible for result in results):
            return (math.inf, item)
        return (max(result.primary_cost for result in results), item)

    def concrete_probe(self, row, planners):
        assertion = row['assertion']
        kind = assertion['kind']
        if kind == 'name':
            costs = dict.fromkeys(VERSIONS, 0)
            return {'method': 'check_name', 'params': {'name': assertion['item']}}, costs, 1, None
        if kind == 'craftability':
            item = assertion['item']
            results = {v: self.target_plan(planners[v], item) for v in VERSIONS}
            expected = row['truth_vector']
            if any(results[v].feasible != expected[v] for v in VERSIONS):
                return None, None, None, 'oracle feasibility disagrees with craftability truth'
            costs = {v: results[v].primary_cost if results[v].feasible else None for v in VERSIONS}
            return {'method': 'task_goal', 'params': {'item': item, 'count': 1}}, costs, 0, None
        if kind == 'drop':
            block = assertion['block']
            if block not in SOURCE_SPECS:
                return None, None, None, 'source block absent from arena'
            tool = SOURCE_SPECS[block]['tool']
            requirements = Counter({tool: 1}) if tool else Counter()
            results = {v: self.bundle_plan(planners[v], requirements) if requirements
                       else planners[v].plan_requirements({'stick': 1}, label='empty_probe')
                       for v in VERSIONS}
            # stick is an already-present zero-cost sentinel for an empty requirement set.
            if not all(result.feasible for result in results.values()):
                return None, None, None, 'mining tool unavailable'
            costs = {v: results[v].primary_cost for v in VERSIONS}
            return {'method': 'mine', 'params': {'node_type': block}}, costs, 1, None

        if kind == 'concrete_grid':
            layout = assertion['layout']
            requirements = Counter(item for item in layout if item)
            return self._probe_from_requirements(
                {'method': 'craft_grid', 'params': {'layout': layout}}, requirements, planners, 1)

        if kind != 'recipe':
            return None, None, None, 'unsupported assertion kind'
        type_rejection = rejection_reason(assertion.get('recipe_type'))
        if type_rejection:
            return None, None, None, type_rejection
        if assertion['station'] not in ('crafting_grid', 'furnace'):
            return None, None, None, 'workstation unavailable to semantic interface'
        if assertion.get('ordered'):
            option_slots = [list(cell) for row_cells in assertion['grid']
                            for cell in row_cells if cell]
            shape = assertion['grid']
        else:
            option_slots = [list(options) for options in assertion['ingredients']]
            shape = None
        choices = []
        for options in option_slots:
            candidates = [option for option in options
                          if option in self.common_names and
                          all(option in planners[v].reachable for v in VERSIONS)]
            if not candidates:
                return None, None, None, 'no common reachable concrete ingredient'
            choices.append(min(candidates, key=lambda item: self.choice_score(item, planners)))
        requirements = Counter(choices)
        if assertion['station'] == 'furnace':
            requirements['coal'] += 1
            probe = {'method': 'smelt', 'params': {'input': choices[0], 'fuel': 'coal', 'n': 1}}
        else:
            if shape is not None:
                layout = [None] * 9
                index = 0
                for row_index, row_cells in enumerate(shape):
                    for column_index, cell in enumerate(row_cells):
                        if cell:
                            layout[row_index * 3 + column_index] = choices[index]
                            index += 1
            else:
                layout = choices + [None] * (9 - len(choices))
            probe = {'method': 'craft_grid', 'params': {'layout': layout}}
        return self._probe_from_requirements(probe, requirements, planners, 1)

    def _probe_from_requirements(self, probe, requirements, planners, probe_cost):
        results = {v: self.bundle_plan(planners[v], requirements) for v in VERSIONS}
        if not all(result.feasible for result in results.values()):
            return None, None, None, 'probe ingredients not reachable in every world'
        costs = {v: results[v].primary_cost for v in VERSIONS}
        return probe, costs, probe_cost, None

    def grade_conflicts(self, planners, phase):
        rows = []
        for original in self.draft:
            row = json.loads(compact(original))
            probe, acquisition, probe_cost, error = self.concrete_probe(row, planners)
            if error:
                grade = 'C'
                totals = dict.fromkeys(VERSIONS, None)
            else:
                totals = {
                    version: (None if acquisition[version] is None
                              else acquisition[version] + probe_cost)
                    for version in VERSIONS
                }
                finite = [cost for cost in totals.values() if cost is not None]
                if row['assertion']['kind'] == 'craftability':
                    # Infeasible worlds intentionally have no action cost.
                    valid = all((totals[v] is not None) == row['truth_vector'][v]
                                for v in VERSIONS)
                else:
                    valid = len(finite) == len(VERSIONS)
                grade = ('A' if valid and max(finite, default=math.inf) <= 40 else
                         'B' if valid else 'C')
            row.update({
                'grade': grade,
                'grade_status': error or f'{phase}; maximum total verification cost '
                                f'{max((x for x in totals.values() if x is not None), default="NA")}',
                'probe': probe,
                'acquisition_cost': acquisition or dict.fromkeys(VERSIONS, None),
                'verification_cost': totals,
                'task_relevant': bool(row.get('subtype') != 'tag_expansion' and
                                      row.get('main_type') != 'invariant_false_control' and
                                      len(set(row['truth_vector'].values())) > 1),
            })
            rows.append(row)
        return rows

    def action_conflicts(self, version, plan, rows_by_id):
        found = set()
        for action in plan.actions:
            if action['method'] in ('craft_grid', 'smelt'):
                raw = self.raw_recipe[version].get(action['recipe_id'])
                if raw:
                    assertion = recipe_assertion(raw)
                    if assertion:
                        candidate = assertion_id(assertion)
                        if candidate in rows_by_id:
                            found.add(candidate)
            elif action['method'] == 'mine':
                expected = action['expected']
                assertion = {'kind': 'drop', 'block': action['node_type'],
                             'silk_touch': False, 'drops': [expected['name']]}
                candidate = assertion_id(assertion)
                if candidate in rows_by_id:
                    found.add(candidate)
        return sorted(found)

    def task_candidates(self, prelim_rows):
        rows_by_id = {row['id']: row for row in prelim_rows}
        outputs = set()
        for version in VERSIONS:
            outputs.update(recipe['output'] for recipe in self.unlimited[version].recipes)
        excluded = set(INITIAL) | {'air'}
        candidates = []
        for target in sorted(outputs - excluded):
            plans = {v: self.target_plan(self.unlimited[v], target) for v in VERSIONS}
            feasible = {v: plans[v].feasible for v in VERSIONS}
            if not feasible['1.21.11']:
                continue
            costs = {v: plans[v].primary_cost if plans[v].feasible else None for v in VERSIONS}
            if costs['1.21.11'] is None or costs['1.21.11'] > 55:
                continue
            paths = {v: self.action_conflicts(v, plans[v], rows_by_id)
                     if plans[v].feasible else [] for v in VERSIONS}
            latest_a = [conflict for conflict in paths['1.21.11']
                        if rows_by_id[conflict]['grade'] == 'A' and
                        rows_by_id[conflict]['task_relevant']]
            all_path = set().union(*map(set, paths.values()))
            main_types = sorted({rows_by_id[c]['main_type'] for c in latest_a})
            old = costs['1.16.5']
            new = costs['1.21.11']
            candidates.append({
                'target': target,
                'plans': plans,
                'feasible': feasible,
                'costs': costs,
                'paths': paths,
                'latest_a': latest_a,
                'main_types': main_types,
                'feasibility_change': len(set(feasible.values())) > 1,
                'efficiency_candidate': bool(old is not None and new and old > 1.5 * new),
                'control': all(feasible.values()) and not all_path,
            })
        return candidates

    def select_tasks(self, candidates):
        selected = []
        used_targets = set()
        conflict_uses = Counter()

        def add(category, candidate):
            used_targets.add(candidate['target'])
            if category == 'planning':
                for conflict in candidate['latest_a']:
                    conflict_uses[conflict] += 1
            selected.append((category, candidate))

        planning = sorted((c for c in candidates if c['latest_a']),
                          key=lambda c: (c['costs']['1.21.11'], c['target']))
        for candidate in planning:
            usable = [c for c in candidate['latest_a'] if conflict_uses[c] < 4]
            if not usable or candidate['target'] in used_targets:
                continue
            candidate = dict(candidate)
            candidate['latest_a'] = usable
            add('planning', candidate)
            if sum(category == 'planning' for category, _ in selected) == 30:
                break

        for candidate in sorted((c for c in candidates if c['feasibility_change']),
                                key=lambda c: (c['costs']['1.21.11'], c['target'])):
            if candidate['target'] not in used_targets:
                add('feasibility', candidate)
            if sum(category == 'feasibility' for category, _ in selected) == 10:
                break

        for candidate in sorted((c for c in candidates if c['efficiency_candidate']),
                                key=lambda c: (-c['costs']['1.16.5'] /
                                               c['costs']['1.21.11'], c['target'])):
            if candidate['target'] not in used_targets:
                add('efficiency', candidate)
            if sum(category == 'efficiency' for category, _ in selected) == 8:
                break

        for candidate in sorted((c for c in candidates if c['control']),
                                key=lambda c: (c['costs']['1.21.11'], c['target'])):
            if candidate['target'] not in used_targets:
                add('control', candidate)
            if sum(category == 'control' for category, _ in selected) == 12:
                break
        return selected

    def capacities_from_tasks(self, selected):
        maxima = Counter()
        for _, candidate in selected:
            for version in VERSIONS:
                plan = candidate['plans'][version]
                if plan.feasible:
                    for block, count in plan.resource_demand.items():
                        maxima[block] = max(maxima[block], count)
        capacities = {block: max(1, 2 * maxima[block]) for block in SOURCE_SPECS}
        return maxima, capacities

    def finalize_tasks(self, selected, planners, rows):
        rows_by_id = {row['id']: row for row in rows}
        provisional = []
        for category, candidate in selected:
            target = candidate['target']
            plans = {v: self.target_plan(planners[v], target) for v in VERSIONS}
            feasible = {v: plans[v].feasible for v in VERSIONS}
            costs = {v: plans[v].primary_cost if plans[v].feasible else None for v in VERSIONS}
            paths = {v: self.action_conflicts(v, plans[v], rows_by_id)
                     if plans[v].feasible else [] for v in VERSIONS}
            max_cost = max(cost for cost in costs.values() if cost is not None)
            conflict_ids = sorted(set().union(*map(set, paths.values())))
            bypass = (target not in INITIAL and
                      all(item not in {'iron_ingot', 'gold_ingot', 'raw_iron', 'raw_gold',
                                       'netherite_ingot', 'copper_ingot', 'cobbled_deepslate'}
                          for item in INITIAL))
            provisional.append({
                'id': '',
                'type': category,
                'target': {'item': target, 'count': 1},
                'standard_items': [{'name': k, 'count': v} for k, v in sorted(INITIAL.items())],
                'task_supplements': [],
                'supplement_bypass_check': {'passed': bypass,
                    'rule': 'goal is not supplied; prohibited conflict intermediates absent'},
                'action_budget': min(120, math.ceil(2 * max_cost) + 10),
                'wall_clock_seconds': 300,
                'feasible': feasible,
                'oracle_cost': costs,
                'conflict_ids': conflict_ids,
                'conflicts_by_world': paths,
                'oracle_plans': {v: plans[v].as_dict() if plans[v].feasible else None
                                 for v in VERSIONS},
                'split': 'eval',
            })
        counts = Counter()
        for task in provisional:
            counts[task['type']] += 1
            task['id'] = f"{task['type'][:3]}_{counts[task['type']]:03d}"

        rng = random.Random(20261004)
        planning = [task for task in provisional if task['type'] == 'planning']
        buckets = defaultdict(list)
        for task in planning:
            types = sorted({rows_by_id[c]['main_type'] for c in task['conflict_ids']
                            if c in rows_by_id})
            buckets[types[0] if types else 'unknown'].append(task)
        dev = []
        while len(dev) < min(12, len(planning)):
            progressed = False
            for key in sorted(buckets):
                remaining = [task for task in buckets[key] if task not in dev]
                if remaining and len(dev) < 12:
                    dev.append(rng.choice(remaining))
                    progressed = True
            if not progressed:
                break
        for task in dev:
            task['split'] = 'dev'
        return provisional

    def run(self):
        preliminary = self.grade_conflicts(self.unlimited, 'unlimited candidate phase')
        candidates = self.task_candidates(preliminary)
        selected = self.select_tasks(candidates)
        maxima, capacities = self.capacities_from_tasks(selected)
        final_planners = {v: OraclePlanner(v, capacities=capacities) for v in VERSIONS}
        final_rows = self.grade_conflicts(final_planners, 'final finite-array phase')
        tasks = self.finalize_tasks(selected, final_planners, final_rows)

        version_category = {}
        recipe_type_c_by_version = {}
        for version in VERSIONS:
            category_rows = {}
            for category in sorted({row['main_type'] for row in final_rows}):
                present = [row for row in final_rows
                           if row['main_type'] == category and row['truth_vector'][version]]
                category_rows[category] = {
                    'executable_grade_A_or_B': sum(row['grade'] in {'A', 'B'} for row in present),
                    'grade_C': sum(row['grade'] == 'C' for row in present),
                    'assertions_true_in_version': len(present),
                }
            version_category[version] = category_rows
            recipe_type_c_by_version[version] = dict(Counter(
                row['assertion'].get('recipe_type')
                for row in final_rows
                if row['grade'] == 'C' and row['truth_vector'][version]
                and row['assertion'].get('kind') == 'recipe'))

        data = ROOT / 'data'
        with (data / 'conflicts.jsonl').open('w') as handle:
            for row in final_rows:
                handle.write(compact(row) + '\n')
        with (data / 'tasks.jsonl').open('w') as handle:
            for task in tasks:
                handle.write(compact(task) + '\n')

        report = {
            'status': 'GENERATED_REQUIRES_LIVE_VALIDATION_AND_PREREGISTRATION',
            'development_seed': 20261004,
            'candidate_items': len(candidates),
            'selected_task_counts': dict(Counter(task['type'] for task in tasks)),
            'split_counts': dict(Counter(task['split'] for task in tasks)),
            'resource_maximum_candidate_demand': dict(sorted(maxima.items())),
            'resource_array_capacities': capacities,
            'conflict_grade_counts': dict(Counter(row['grade'] for row in final_rows)),
            'tag_expansion_count': sum(row.get('subtype') == 'tag_expansion' for row in final_rows),
            'planning_conflict_max_reuse': max(Counter(
                conflict for task in tasks if task['type'] == 'planning'
                for conflict in task['conflict_ids']).values(), default=0),
            'unique_targets': len({task['target']['item'] for task in tasks}),
            'version_by_conflict_category': version_category,
            'grade_c_recipe_types_true_by_version': recipe_type_c_by_version,
            'limitations': [
                'Generated plans have passed symbolic inventory replay but not yet game execution.',
                'The task count may be below suggested counts when the finite executable corpus lacks candidates.',
                'Resource capacities are not frozen until arena reset hashes and live development checks pass.',
            ],
        }
        (ROOT / 'reports/stage1_data_build.json').write_text(json.dumps(report, indent=2) + '\n')
        resource = {
            'schema_version': 2,
            'status': 'GENERATED_PENDING_LIVE_VALIDATION_AND_PREREGISTRATION',
            'arena_source': 'harness/config/arena.json',
            'R0_definition': 'Common starting items plus common-ID source blocks; mined outputs are private world truth.',
            'yield_semantics': 'Guaranteed minimum named output only; probabilistic-only identities are unreachable.',
            'executable_mechanisms': ['crafting_grid', 'furnace', 'mining'],
            'standard_inventory': [{'name': k, 'count': v} for k, v in sorted(INITIAL.items())],
            'global_supplements': [{'name': k, 'count': INITIAL[k]}
                                   for k in ('leather', 'string', 'paper', 'chiseled_stone_bricks')],
            'prohibited_supplements': ['iron_ingot', 'gold_ingot', 'raw_iron', 'raw_gold',
                                       'netherite_ingot', 'copper_ingot', 'cobbled_deepslate'],
            'candidate_maximum_demand': dict(sorted(maxima.items())),
            'array_rule': 'For each source block: max(1, 2 * maximum demand over selected candidate task/world plans).',
            'resource_block_counts': capacities,
            'identical_across_worlds_and_methods': True,
            'replenishment': 'episode reset only',
        }
        (ROOT / 'config/resource_model.json').write_text(json.dumps(resource, indent=2) + '\n')
        print(json.dumps(report))


if __name__ == '__main__':
    Builder().run()
