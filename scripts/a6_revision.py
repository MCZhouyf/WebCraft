#!/usr/bin/env python3
"""Diagnose and revise the Stage 1 task corpus before live validation."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import math
import random
import shutil
import sys
from collections import Counter, defaultdict
from functools import lru_cache
from pathlib import Path

import pulp

ROOT = Path('/root/autodl-tmp/webcraft')
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

from build_stage1_data import Builder, compact  # noqa: E402
from conflict_core import accepts_layout, recipe_assertion  # noqa: E402
from oracle.planner import INITIAL, VERSIONS, OraclePlanner, validate_plan  # noqa: E402

DEV_SEED = 20261005
OLD_TASK_SHA256 = '88ca6d053ff9bf7b2bd3798a09f86692d4fe754e396a187cb08f2e95946956de'
ARCHIVE = ROOT / 'data/archive/a6_pre_revision_20261005'


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open('rb') as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b''):
            digest.update(block)
    return digest.hexdigest()


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text(''.join(compact(row) + '\n' for row in rows))


def mechanism_label(row: dict) -> str:
    assertion = row['assertion']
    kind = assertion['kind']
    if kind == 'drop':
        detail = f"drop:{assertion['block']}=>{','.join(assertion.get('drops', []))}"
    elif kind in {'recipe', 'concrete_grid'}:
        detail = f"{kind}:{assertion.get('recipe_type')}:{assertion.get('output')}"
    elif kind in {'name', 'craftability'}:
        detail = f"{kind}:{assertion.get('item')}"
    else:
        detail = kind
    return f"{row['main_type']}|{detail}|{row['id']}"


class A6Revision:
    def __init__(self):
        self.builder = Builder()
        self.capacities = json.loads(
            (ROOT / 'config/resource_model.json').read_text()
        )['resource_block_counts']
        self.planners = {
            version: OraclePlanner(version, capacities=self.capacities)
            for version in VERSIONS
        }
        self.plan_cache: dict[tuple[str, str], object] = {}
        self.preliminary_rows: list[dict] | None = None
        self.final_rows = [
            json.loads(line)
            for line in (ROOT / 'data/conflicts.jsonl').read_text().splitlines()
            if line
        ]
        self.rows_by_id = {row['id']: row for row in self.final_rows}
        self.raw_recipe = {
            version: {recipe['id']: recipe for recipe in self.builder.truth[version]['recipes']}
            for version in VERSIONS
        }

    def plan(self, version: str, target: str):
        key = (version, target)
        if key not in self.plan_cache:
            result = self.planners[version].plan(target)
            if result.feasible:
                validate_plan(result)
            self.plan_cache[key] = result
        return self.plan_cache[key]

    def all_targets(self) -> list[str]:
        names = set().union(*(planner.names for planner in self.planners.values()))
        outputs = set().union(*(
            {recipe['output'] for recipe in planner.recipes}
            for planner in self.planners.values()
        ))
        return sorted((names | outputs) - set(INITIAL) - {'air'})

    def paths(self, plans: dict[str, object], rows_by_id: dict[str, dict]) -> dict[str, list[str]]:
        return {
            version: self.builder.action_conflicts(version, plans[version], rows_by_id)
            if plans[version].feasible else []
            for version in VERSIONS
        }

    def planning_candidates(self, rows: list[dict], planners: dict[str, OraclePlanner]) -> list[dict]:
        rows_by_id = {row['id']: row for row in rows}
        outputs = set().union(*(
            {recipe['output'] for recipe in planner.recipes}
            for planner in planners.values()
        ))
        candidates = []
        for target in sorted(outputs - set(INITIAL) - {'air'}):
            plans = {
                version: (self.plan(version, target) if planners is self.planners
                          else self.builder.target_plan(planners[version], target))
                for version in VERSIONS
            }
            feasible = {version: plans[version].feasible for version in VERSIONS}
            if not feasible['1.21.11']:
                continue
            costs = {
                version: plans[version].primary_cost if plans[version].feasible else None
                for version in VERSIONS
            }
            if costs['1.21.11'] is None or costs['1.21.11'] > 55:
                continue
            paths = self.paths(plans, rows_by_id)
            latest_a = [
                conflict for conflict in paths['1.21.11']
                if rows_by_id[conflict]['grade'] == 'A'
                and rows_by_id[conflict]['task_relevant']
            ]
            all_path = set().union(*map(set, paths.values()))
            candidates.append({
                'target': target,
                'plans': plans,
                'feasible': feasible,
                'costs': costs,
                'paths': paths,
                'latest_a': sorted(latest_a),
                'control': all(feasible.values()) and not all_path,
            })
        return candidates

    @staticmethod
    def strict_planning_selection(candidates: list[dict], cap: int,
                                  excluded_targets: set[str] | None = None,
                                  limit: int = 30) -> tuple[list[dict], Counter]:
        excluded_targets = excluded_targets or set()
        candidates = [
            candidate for candidate in candidates
            if candidate['latest_a'] and candidate['target'] not in excluded_targets
        ]
        model = pulp.LpProblem(f'a6_planning_cap_{cap}', pulp.LpMaximize)
        choose = {
            index: pulp.LpVariable(f'candidate_{index}', cat='Binary')
            for index in range(len(candidates))
        }
        mechanisms = sorted({
            conflict for candidate in candidates for conflict in candidate['latest_a']
        })
        covered = {
            conflict: pulp.LpVariable(f'mechanism_{index}', cat='Binary')
            for index, conflict in enumerate(mechanisms)
        }
        for conflict in mechanisms:
            members = [
                choose[index] for index, candidate in enumerate(candidates)
                if conflict in candidate['latest_a']
            ]
            model += pulp.lpSum(members) <= cap
            model += covered[conflict] <= pulp.lpSum(members)
        model += pulp.lpSum(choose.values()) <= limit
        # Cardinality dominates mechanism coverage, which dominates deterministic rank.
        model += (
            1_000_000_000 * pulp.lpSum(choose.values())
            + 1_000_000 * pulp.lpSum(covered.values())
            + pulp.lpSum((len(candidates) - index) * variable
                         for index, variable in choose.items())
        )
        status = model.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))
        if pulp.LpStatus[status] != 'Optimal':
            raise RuntimeError(f'planning selection failed: {pulp.LpStatus[status]}')
        selected = [
            candidate for index, candidate in enumerate(candidates)
            if pulp.value(choose[index]) > 0.5
        ]
        selected.sort(key=lambda row: (row['costs']['1.21.11'], row['target']))
        frequencies = Counter(
            conflict for candidate in selected for conflict in set(candidate['latest_a'])
        )
        return selected, frequencies

    def diagnose(self, write: bool = True) -> dict:
        preliminary = self.builder.grade_conflicts(
            self.builder.unlimited, 'A6 read-only diagnosis'
        )
        self.preliminary_rows = preliminary
        rows_by_id = {row['id']: row for row in preliminary}
        outputs = set().union(*(
            {recipe['output'] for recipe in self.builder.unlimited[version].recipes}
            for version in VERSIONS
        ))
        base = sorted(outputs - set(INITIAL) - {'air'})
        funnel = {
            'recipe_outputs_union': len(outputs),
            'after_initial_and_air_exclusion': len(base),
            'latest_world_feasible': 0,
            'latest_world_cost_at_most_55': 0,
            'planning_candidates_with_relevant_grade_A_conflict': 0,
        }
        candidates = self.planning_candidates(preliminary, self.builder.unlimited)
        for target in base:
            result = self.builder.target_plan(self.builder.unlimited['1.21.11'], target)
            if result.feasible:
                funnel['latest_world_feasible'] += 1
                if result.primary_cost <= 55:
                    funnel['latest_world_cost_at_most_55'] += 1
        planning = [candidate for candidate in candidates if candidate['latest_a']]
        funnel['planning_candidates_with_relevant_grade_A_conflict'] = len(planning)
        all_frequency = Counter(
            conflict for candidate in planning for conflict in set(candidate['latest_a'])
        )
        cap4, cap4_frequency = self.strict_planning_selection(planning, 4)
        cap6, cap6_frequency = self.strict_planning_selection(planning, 6)
        report = {
            'status': 'A6_DIAGNOSIS_COMPLETE_BEFORE_TASK_REVISION',
            'funnel': funnel,
            'planning_candidate_count': len(planning),
            'unique_conflict_mechanisms': len(all_frequency),
            'mechanism_frequency_all_candidates': {
                mechanism_label(rows_by_id[conflict]): count
                for conflict, count in all_frequency.most_common()
            },
            'strict_cap_results': {
                '4': {
                    'maximum_tasks': len(cap4),
                    'mechanisms': len(cap4_frequency),
                    'maximum_reuse': max(cap4_frequency.values(), default=0),
                },
                '6': {
                    'maximum_tasks': len(cap6),
                    'mechanisms': len(cap6_frequency),
                    'maximum_reuse': max(cap6_frequency.values(), default=0),
                },
            },
            'binding_constraint': (
                'Full-path mechanism reuse is binding: 83 of 93 planning candidates '
                'contain the iron_ore=>raw_iron replacement and 18 contain the '
                'gold_ore=>raw_gold replacement. Strict cap 6 cannot produce 29 total '
                'planning tasks needed for 24 eval plus 5 dev tasks.'
            ),
        }
        if write:
            reports = ROOT / 'reports'
            (reports / 'a6_diagnosis.json').write_text(json.dumps(report, indent=2) + '\n')
            with (reports / 'a6_filter_funnel.csv').open('w', newline='') as handle:
                writer = csv.writer(handle, lineterminator='\n')
                writer.writerow(['stage', 'count'])
                writer.writerows(funnel.items())
            with (reports / 'a6_mechanism_frequency.csv').open('w', newline='') as handle:
                writer = csv.writer(handle, lineterminator='\n')
                writer.writerow(['conflict_id', 'mechanism', 'planning_candidate_count'])
                for conflict, count in all_frequency.most_common():
                    writer.writerow([conflict, mechanism_label(rows_by_id[conflict]), count])
            with (reports / 'a6_planning_candidate_paths.csv').open('w', newline='') as handle:
                writer = csv.writer(handle, lineterminator='\n')
                writer.writerow(['target', 'latest_cost', 'grade_a_conflict_ids',
                                 'grade_a_mechanisms'])
                for candidate in sorted(planning,
                                        key=lambda row: (row['costs']['1.21.11'],
                                                         row['target'])):
                    writer.writerow([
                        candidate['target'], candidate['costs']['1.21.11'],
                        ';'.join(candidate['latest_a']),
                        ';'.join(mechanism_label(rows_by_id[conflict])
                                 for conflict in candidate['latest_a']),
                    ])
        return report

    @lru_cache(maxsize=None)
    def matching_craft(self, version: str, layout: tuple[str | None, ...]):
        matches = []
        for recipe in self.planners[version].recipes:
            if recipe['station'] != 'crafting_grid':
                continue
            assertion = recipe_assertion(recipe['raw'])
            if assertion and accepts_layout(assertion, list(layout)):
                matches.append(recipe)
        return sorted(matches, key=lambda recipe: recipe['id'])

    def replay(self, plan, target_version: str, target: str) -> dict:
        if not plan.feasible:
            return {
                'feasible': False, 'cost': None, 'stale_action': True,
                'failure_reason': 'no_stale_world_plan',
            }
        planner = self.planners[target_version]
        inventory = Counter(INITIAL)
        remaining = Counter(self.capacities)
        cost = 0
        stale_action = False
        for index, action in enumerate(plan.actions):
            method = action['method']
            if method == 'mine':
                source = planner.sources.get(action['node_type'])
                if not source:
                    return self._replay_failure(index, 'mine_unavailable', cost)
                if remaining[action['node_type']] <= 0:
                    return self._replay_failure(index, 'outside_arena_capacity', cost)
                if source['tool'] and inventory[source['tool']] < 1:
                    return self._replay_failure(index, 'missing_tool', cost)
                remaining[action['node_type']] -= 1
                inventory[source['output']] += source['output_count']
                cost += 1
                stale_action |= (
                    source['output'] != action['expected']['name']
                    or source['output_count'] != action['expected']['count']
                )
            elif method == 'craft_grid':
                needed = Counter(item for item in action['layout'] if item)
                if any(inventory[item] < count for item, count in needed.items()):
                    return self._replay_failure(index, 'missing_craft_input', cost)
                matches = self.matching_craft(target_version, tuple(action['layout']))
                if not matches:
                    return self._replay_failure(index, 'craft_layout_unavailable', cost)
                recipe = matches[0]
                inventory.subtract(needed)
                inventory[recipe['output']] += recipe['output_count']
                cost += 1
                stale_action |= (
                    recipe['output'] != action['expected']['name']
                    or recipe['output_count'] != action['expected']['count']
                )
            elif method == 'smelt':
                count = action['n']
                fuel_count = action['fuel_count']
                if (inventory[action['input']] < count
                        or inventory['coal'] < fuel_count):
                    return self._replay_failure(index, 'missing_smelt_input_or_fuel', cost)
                matches = sorted((
                    recipe for recipe in planner.recipes
                    if recipe['station'] == 'furnace'
                    and action['input'] in recipe['slots'][0]
                ), key=lambda recipe: recipe['id'])
                if not matches:
                    return self._replay_failure(index, 'smelt_recipe_unavailable', cost)
                recipe = matches[0]
                inventory[action['input']] -= count
                inventory['coal'] -= fuel_count
                output_count = recipe['output_count'] * count
                inventory[recipe['output']] += output_count
                cost += count
                stale_action |= (
                    recipe['output'] != action['expected']['name']
                    or output_count != action['expected']['count']
                )
            else:
                return self._replay_failure(index, f'unsupported_method:{method}', cost)
        if inventory[target] < 1:
            return self._replay_failure(len(plan.actions), 'goal_not_produced', cost)
        return {
            'feasible': True, 'cost': cost, 'stale_action': stale_action,
            'failure_reason': None,
        }

    @staticmethod
    def _replay_failure(action_index: int, reason: str, cost: int) -> dict:
        return {
            'feasible': False, 'cost': None, 'stale_action': True,
            'failure_reason': reason, 'failed_action_index': action_index,
            'executed_cost_before_failure': cost,
        }

    def feasibility_candidates(self) -> list[dict]:
        rows = []
        for target in self.all_targets():
            plans = {version: self.plan(version, target) for version in VERSIONS}
            feasible = {version: plans[version].feasible for version in VERSIONS}
            if len(set(feasible.values())) <= 1:
                continue
            rows.append({
                'target': target,
                'plans': plans,
                'feasible': feasible,
                'costs': {
                    version: plans[version].primary_cost if plans[version].feasible else None
                    for version in VERSIONS
                },
            })
        return rows

    @staticmethod
    def select_feasibility(candidates: list[dict], count: int = 10) -> list[dict]:
        if len(candidates) < count:
            return candidates
        model = pulp.LpProblem('a6_feasibility_balance', pulp.LpMaximize)
        choose = {
            index: pulp.LpVariable(f'feasibility_{index}', cat='Binary')
            for index in range(len(candidates))
        }
        model += pulp.lpSum(choose.values()) == count
        lower = math.ceil(0.30 * count)
        upper = math.floor(0.70 * count)
        for version in ('1.16.5', '1.21.11'):
            feasible_count = pulp.lpSum(
                choose[index] for index, candidate in enumerate(candidates)
                if candidate['feasible'][version]
            )
            model += feasible_count >= lower
            model += feasible_count <= upper
        mandatory = next((index for index, candidate in enumerate(candidates)
                          if candidate['target'] == 'lodestone'), None)
        if mandatory is None:
            raise RuntimeError('mandatory lodestone feasibility candidate is absent')
        model += choose[mandatory] == 1
        # Prefer low-cost, deterministic targets after satisfying exact balance.
        model += pulp.lpSum((len(candidates) - index) * variable
                            for index, variable in choose.items())
        status = model.solve(pulp.PULP_CBC_CMD(msg=False, threads=1))
        if pulp.LpStatus[status] != 'Optimal':
            raise RuntimeError(f'feasibility selection failed: {pulp.LpStatus[status]}')
        return [candidate for index, candidate in enumerate(candidates)
                if pulp.value(choose[index]) > 0.5]

    def efficiency_candidates(self, excluded_targets: set[str]) -> list[dict]:
        rows = []
        for target in self.all_targets():
            if target in excluded_targets:
                continue
            plans = {version: self.plan(version, target) for version in VERSIONS}
            for world in VERSIONS:
                true_plan = plans[world]
                if not true_plan.feasible or true_plan.primary_cost > 55:
                    continue
                for stale_world in VERSIONS:
                    if stale_world == world:
                        continue
                    replay = self.replay(plans[stale_world], world, target)
                    qualifies = (
                        not replay['feasible']
                        or replay['cost'] >= 1.5 * true_plan.primary_cost
                    )
                    if not qualifies:
                        continue
                    ratio = (None if replay['cost'] is None
                             else replay['cost'] / true_plan.primary_cost)
                    rows.append({
                        'target': target,
                        'plans': plans,
                        'feasible': {v: plans[v].feasible for v in VERSIONS},
                        'costs': {v: plans[v].primary_cost if plans[v].feasible else None
                                  for v in VERSIONS},
                        'efficiency': {
                            'primary_world': world,
                            'stale_source_world': stale_world,
                            'c_star': true_plan.primary_cost,
                            'c_stale': replay['cost'],
                            'stale_replay_feasible': replay['feasible'],
                            'stale_failure_reason': replay['failure_reason'],
                            'cost_ratio': ratio,
                        },
                    })
        return rows

    @staticmethod
    def select_efficiency(candidates: list[dict], count: int = 8) -> list[dict]:
        # Keep one task per target and spread selected pairs across world directions.
        ordered = sorted(candidates, key=lambda row: (
            row['efficiency']['stale_replay_feasible'],
            -(row['efficiency']['cost_ratio'] or math.inf),
            row['target'], row['efficiency']['primary_world'],
            row['efficiency']['stale_source_world'],
        ))
        selected = []
        targets = set()
        pair_uses = Counter()
        while len(selected) < min(count, len({row['target'] for row in ordered})):
            eligible = [
                row for row in ordered if row['target'] not in targets
            ]
            if not eligible:
                break
            row = min(eligible, key=lambda candidate: (
                pair_uses[(candidate['efficiency']['primary_world'],
                           candidate['efficiency']['stale_source_world'])],
                candidate['efficiency']['stale_replay_feasible'],
                -(candidate['efficiency']['cost_ratio'] or math.inf),
                candidate['target'],
            ))
            selected.append(row)
            targets.add(row['target'])
            pair_uses[(row['efficiency']['primary_world'],
                       row['efficiency']['stale_source_world'])] += 1
        return selected

    def finalize_task(self, category: str, candidate: dict) -> dict:
        target = candidate['target']
        plans = {version: self.plan(version, target) for version in VERSIONS}
        feasible = {version: plans[version].feasible for version in VERSIONS}
        costs = {
            version: plans[version].primary_cost if plans[version].feasible else None
            for version in VERSIONS
        }
        paths = self.paths(plans, self.rows_by_id)
        conflict_ids = sorted(set().union(*map(set, paths.values())))
        max_cost = max(cost for cost in costs.values() if cost is not None)
        task = {
            'id': '',
            'type': category,
            'target': {'item': target, 'count': 1},
            'standard_items': [
                {'name': name, 'count': count} for name, count in sorted(INITIAL.items())
            ],
            'task_supplements': [],
            'supplement_bypass_check': {
                'passed': target not in INITIAL,
                'rule': 'goal is not supplied; prohibited conflict intermediates absent',
            },
            'action_budget': min(120, math.ceil(2 * max_cost) + 10),
            'wall_clock_seconds': 300,
            'feasible': feasible,
            'oracle_cost': costs,
            'conflict_ids': conflict_ids,
            'conflicts_by_world': paths,
            'oracle_plans': {
                version: plans[version].as_dict() if plans[version].feasible else None
                for version in VERSIONS
            },
            'split': 'eval',
        }
        if category == 'efficiency':
            task['efficiency'] = candidate['efficiency']
        if category == 'feasibility':
            task['feasibility_vector'] = feasible
        return task

    def assign_ids_and_dev(self, tasks: list[dict]) -> None:
        prefixes = {'planning': 'pla', 'feasibility': 'fea',
                    'efficiency': 'eff', 'control': 'con'}
        counts = Counter()
        for task in tasks:
            counts[task['type']] += 1
            task['id'] = f"{prefixes[task['type']]}_{counts[task['type']]:03d}"
            task['split'] = 'eval'
        quotas = {'planning': 5, 'feasibility': 2, 'efficiency': 1}
        rng = random.Random(DEV_SEED)
        mechanism_frequency = Counter(
            conflict for task in tasks if task['type'] == 'planning'
            for conflict in task['conflicts_by_world']['1.21.11']
            if conflict in self.rows_by_id and self.rows_by_id[conflict]['grade'] == 'A'
        )
        for category, quota in quotas.items():
            members = [task for task in tasks if task['type'] == category]
            if category == 'planning':
                key = lambda task: min(
                    (mechanism_frequency[conflict], conflict)
                    for conflict in task['conflicts_by_world']['1.21.11']
                    if conflict in mechanism_frequency
                )[1]
            elif category == 'feasibility':
                key = lambda task: ''.join('1' if task['feasible'][version] else '0'
                                           for version in VERSIONS)
            else:
                key = lambda task: (
                    task['efficiency']['primary_world'] + '<-'
                    + task['efficiency']['stale_source_world']
                )
            buckets = defaultdict(list)
            for task in members:
                buckets[key(task)].append(task)
            for bucket in buckets.values():
                bucket.sort(key=lambda task: task['id'])
                rng.shuffle(bucket)
            chosen = []
            while len(chosen) < min(quota, len(members)):
                progressed = False
                for bucket_key in sorted(buckets):
                    remaining = [task for task in buckets[bucket_key] if task not in chosen]
                    if remaining and len(chosen) < quota:
                        chosen.append(remaining[0])
                        progressed = True
                if not progressed:
                    break
            for task in chosen:
                task['split'] = 'dev'

    def stale_expectation(self, task: dict) -> dict:
        target = task['target']['item']
        old_plan = self.plan('1.16.5', target)
        current_plan = self.plan('1.21.11', target)
        replay = self.replay(old_plan, '1.21.11', target)
        conflict_types = sorted({
            self.rows_by_id[conflict]['main_type']
            for conflict in task['conflict_ids'] if conflict in self.rows_by_id
        })
        feasibility_disagrees = old_plan.feasible != current_plan.feasible
        stale_types = {'replacement', 'removed', 'renamed', 'feasibility_change'}
        expected_stale_action = bool(
            feasibility_disagrees or replay['stale_action']
            or stale_types.intersection(conflict_types)
        )
        if task['type'] == 'feasibility' and not current_plan.feasible:
            expected_success = not old_plan.feasible
        else:
            expected_success = bool(current_plan.feasible and replay['feasible'])
        if conflict_types and set(conflict_types) <= {'new_route'} and replay['feasible']:
            expected_stale_action = False
        return {
            'task_id': task['id'],
            'target': target,
            'task_type': task['type'],
            'split': task['split'],
            'source_truth_version': '1.16.5',
            'execution_world': '1.21.11',
            'conflict_types': conflict_types,
            'old_plan_available': old_plan.feasible,
            'current_world_feasible': current_plan.feasible,
            'expected_stale_action': expected_stale_action,
            'expected_success': expected_success,
            'replay_feasible': replay['feasible'],
            'replay_cost': replay['cost'],
            'failure_reason': replay['failure_reason'],
            'rationale': (
                'new_route-only paths may retain a valid old route; replacement, removal, '
                'rename, feasibility disagreement, or replay mismatch is stale.'
            ),
        }

    def snapshot_old_tasks(self) -> dict:
        ARCHIVE.mkdir(parents=True, exist_ok=True)
        source_files = [ROOT / 'data/tasks.jsonl', ROOT / 'reports/stage1_data_build.json']
        manifest = {}
        for source in source_files:
            destination = ARCHIVE / source.name
            if not destination.exists():
                shutil.copy2(source, destination)
            manifest[source.name] = {
                'sha256': sha256(destination), 'bytes': destination.stat().st_size,
            }
        if manifest['tasks.jsonl']['sha256'] != OLD_TASK_SHA256:
            raise RuntimeError('pre-A6 task snapshot does not match recorded A3-A5 hash')
        (ARCHIVE / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
        return manifest

    def apply(self) -> dict:
        diagnosis = self.diagnose(write=True)
        old_manifest = self.snapshot_old_tasks()

        feasibility_pool = self.feasibility_candidates()
        feasibility = self.select_feasibility(feasibility_pool, 10)
        used_targets = {candidate['target'] for candidate in feasibility}

        efficiency_pool = self.efficiency_candidates(used_targets)
        efficiency = self.select_efficiency(efficiency_pool, 8)
        used_targets.update(candidate['target'] for candidate in efficiency)

        planning_pool = self.planning_candidates(self.final_rows, self.planners)
        planning_cap4, _ = self.strict_planning_selection(
            planning_pool, 4, excluded_targets=used_targets
        )
        cap = 4 if max(0, len(planning_cap4) - 5) >= 24 else 6
        planning, planning_frequency = self.strict_planning_selection(
            planning_pool, cap, excluded_targets=used_targets
        )
        used_targets.update(candidate['target'] for candidate in planning)

        controls = [
            candidate for candidate in planning_pool
            if candidate['control'] and candidate['target'] not in used_targets
        ][:12]

        selected = (
            [('planning', candidate) for candidate in planning]
            + [('feasibility', candidate) for candidate in feasibility]
            + [('efficiency', candidate) for candidate in efficiency]
            + [('control', candidate) for candidate in controls]
        )
        tasks = [self.finalize_task(category, candidate) for category, candidate in selected]
        self.assign_ids_and_dev(tasks)
        expectations = [self.stale_expectation(task) for task in tasks]

        write_jsonl(ROOT / 'data/tasks.jsonl', tasks)
        write_jsonl(ROOT / 'data/stale_agent_expectations.jsonl', expectations)

        eval_planning = [task for task in tasks
                         if task['type'] == 'planning' and task['split'] == 'eval']
        eval_mechanisms = Counter(
            conflict for task in eval_planning
            for conflict in task['conflicts_by_world']['1.21.11']
            if conflict in self.rows_by_id and self.rows_by_id[conflict]['grade'] == 'A'
        )
        feasibility_balance = {
            version: {
                'feasible': sum(candidate['feasible'][version] for candidate in feasibility),
                'infeasible': sum(not candidate['feasible'][version]
                                  for candidate in feasibility),
            }
            for version in ('1.16.5', '1.21.11')
        }
        report = {
            'status': 'A6_COMPLETE_REQUIRES_LIVE_VALIDATION_AND_PREREGISTRATION',
            'diagnosis_status': diagnosis['status'],
            'old_snapshot': str(ARCHIVE.relative_to(ROOT)),
            'old_manifest': old_manifest,
            'development_seed': DEV_SEED,
            'planning_reuse_cap': cap,
            'planning_count_total': len(planning),
            'planning_count_eval': len(eval_planning),
            'planning_target_met': len(eval_planning) >= 24,
            'planning_unique_mechanisms': len(planning_frequency),
            'planning_maximum_full_path_reuse': max(planning_frequency.values(), default=0),
            'planning_eval_mechanism_prevalence': {
                mechanism_label(self.rows_by_id[conflict]): {
                    'tasks': count,
                    'proportion': count / len(eval_planning) if eval_planning else None,
                }
                for conflict, count in eval_mechanisms.most_common()
            },
            'planning_shortfall_reason': None if len(eval_planning) >= 24 else (
                'Strict full-path cap 6 is binding because the iron_ore=>raw_iron '
                'replacement is shared by most candidates; no other threshold was lowered.'
            ),
            'feasibility_pool_count': len(feasibility_pool),
            'feasibility_count': len(feasibility),
            'feasibility_targets': [candidate['target'] for candidate in feasibility],
            'feasibility_balance': feasibility_balance,
            'lodestone_included': any(candidate['target'] == 'lodestone'
                                      for candidate in feasibility),
            'efficiency_candidate_pairs': len(efficiency_pool),
            'efficiency_unique_candidate_targets': len({candidate['target']
                                                        for candidate in efficiency_pool}),
            'efficiency_count': len(efficiency),
            'efficiency_opr_status': ('secondary' if len(efficiency) >= 4 else 'descriptive'),
            'selected_task_counts': dict(Counter(task['type'] for task in tasks)),
            'split_counts': dict(Counter(task['split'] for task in tasks)),
            'dev_type_counts': dict(Counter(task['type'] for task in tasks
                                            if task['split'] == 'dev')),
            'task_sha256': sha256(ROOT / 'data/tasks.jsonl'),
            'stale_expectations_sha256': sha256(
                ROOT / 'data/stale_agent_expectations.jsonl'
            ),
            'conflicts_sha256': sha256(ROOT / 'data/conflicts.jsonl'),
            'resource_model_sha256': sha256(ROOT / 'config/resource_model.json'),
        }
        (ROOT / 'reports/a6_revision.json').write_text(json.dumps(report, indent=2) + '\n')
        print(json.dumps(report))
        return report


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--diagnose-only', action='store_true')
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    if args.diagnose_only == args.apply:
        parser.error('select exactly one of --diagnose-only or --apply')
    revision = A6Revision()
    if args.diagnose_only:
        print(json.dumps(revision.diagnose(write=True)))
    else:
        revision.apply()


if __name__ == '__main__':
    main()
