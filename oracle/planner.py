"""Quantity-aware minimum-primary-cost planner for the semantic WebCraft interface."""
from __future__ import annotations

import copy
import json
import math
from collections import Counter, defaultdict, deque
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pulp


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ('1.16.5', '1.19', '1.21.11')

INITIAL = Counter({
    'wooden_pickaxe': 1,
    'stick': 2,
    'cobblestone': 2,
    'leather': 8,
    'string': 8,
    'paper': 8,
    'chiseled_stone_bricks': 8,
})

# Guaranteed normal-mining yields, checked against each JAR loot table in tests.
SOURCE_SPECS = {
    'oak_log': {'tool': None, 'yield': {'*': ('oak_log', 1)}},
    'cobblestone': {'tool': 'wooden_pickaxe', 'yield': {'*': ('cobblestone', 1)}},
    'stone': {'tool': 'wooden_pickaxe', 'yield': {'*': ('cobblestone', 1)}},
    'iron_ore': {
        'tool': 'stone_pickaxe',
        'yield': {'1.16.5': ('iron_ore', 1), '*': ('raw_iron', 1)},
    },
    'gold_ore': {
        'tool': 'iron_pickaxe',
        'yield': {'1.16.5': ('gold_ore', 1), '*': ('raw_gold', 1)},
    },
    'coal_ore': {'tool': 'wooden_pickaxe', 'yield': {'*': ('coal', 1)}},
    'diamond_ore': {'tool': 'iron_pickaxe', 'yield': {'*': ('diamond', 1)}},
    'redstone_ore': {'tool': 'iron_pickaxe', 'yield': {'*': ('redstone', 4)}},
    'lapis_ore': {'tool': 'stone_pickaxe', 'yield': {'*': ('lapis_lazuli', 4)}},
    'sand': {'tool': None, 'yield': {'*': ('sand', 1)}},
    # Gravel/flint identity is probabilistic without Silk Touch, so neither is guaranteed.
    'gravel': {'tool': None, 'yield': {}},
}


def item_id(value: str | None) -> str | None:
    return value.removeprefix('minecraft:') if isinstance(value, str) else value


def guaranteed_source(version: str, block: str) -> tuple[str, int] | None:
    spec = SOURCE_SPECS[block]
    return spec['yield'].get(version, spec['yield'].get('*'))


def ingredient_slots(recipe: dict[str, Any]) -> list[list[str]]:
    if recipe.get('ordered'):
        slots = []
        for row in recipe.get('pattern', []):
            for symbol in row:
                if symbol != ' ':
                    slots.append(sorted(set(map(item_id, recipe['key'].get(symbol, [])))))
        return slots
    return [sorted(set(map(item_id, options))) for options in recipe.get('ingredients', [])]


def recipe_layout(recipe: dict[str, Any], choices: list[str]) -> list[str | None]:
    if recipe.get('ordered'):
        pattern = recipe.get('pattern', [])
        width = max((len(row) for row in pattern), default=0)
        if len(pattern) > 3 or width > 3:
            raise ValueError('recipe does not fit crafting grid')
        layout: list[str | None] = [None] * 9
        choice_index = 0
        for row_index, row in enumerate(pattern):
            for column_index, symbol in enumerate(row):
                if symbol != ' ':
                    layout[row_index * 3 + column_index] = choices[choice_index]
                    choice_index += 1
        return layout
    if len(choices) > 9:
        raise ValueError('shapeless recipe does not fit crafting grid')
    return choices + [None] * (9 - len(choices))


@dataclass
class PlanResult:
    version: str
    target: str
    quantity: int
    feasible: bool
    primary_cost: int | None
    actions: list[dict[str, Any]]
    resource_demand: dict[str, int]
    final_inventory: dict[str, int]
    recipe_ids: list[str]
    reason: str | None = None
    requirements: dict[str, int] | None = None

    def as_dict(self) -> dict[str, Any]:
        return {
            'version': self.version,
            'target': self.target,
            'quantity': self.quantity,
            'feasible': self.feasible,
            'primary_cost': self.primary_cost,
            'actions': self.actions,
            'resource_demand': self.resource_demand,
            'final_inventory': self.final_inventory,
            'recipe_ids': self.recipe_ids,
            'reason': self.reason,
            'requirements': self.requirements,
        }


class OraclePlanner:
    def __init__(self, version: str, capacities: dict[str, int] | None = None,
                 initial: Counter | None = None, action_limit: int = 120):
        if version not in VERSIONS:
            raise ValueError('unsupported version')
        self.version = version
        self.truth = json.loads((ROOT / f'truth/{version}.json').read_text())
        self.names = set(map(item_id, self.truth['names']['items']))
        self.initial = Counter(initial if initial is not None else INITIAL)
        self.capacities = {
            block: int((capacities or {}).get(block, action_limit)) for block in SOURCE_SPECS
        }
        self.action_limit = action_limit
        self.recipes = []
        for raw in self.truth['recipes']:
            output = item_id(raw['result'].get('item'))
            station = raw.get('station')
            if not output or station not in ('crafting_grid', 'furnace'):
                continue
            slots = ingredient_slots(raw)
            if not slots or any(not slot for slot in slots):
                continue
            if station == 'crafting_grid':
                if raw.get('ordered') and (len(raw.get('pattern', [])) > 3 or
                                           max(map(len, raw.get('pattern', ['']))) > 3):
                    continue
                if not raw.get('ordered') and len(slots) > 9:
                    continue
            self.recipes.append({
                'id': raw['id'],
                'station': station,
                'output': output,
                'output_count': int(raw['result'].get('count', 1)),
                'slots': slots,
                'raw': raw,
            })
        self.by_output = defaultdict(list)
        for recipe in self.recipes:
            self.by_output[recipe['output']].append(recipe)
        self.sources = {}
        for block, spec in SOURCE_SPECS.items():
            produced = guaranteed_source(version, block)
            if produced:
                self.sources[block] = {
                    'block': block,
                    'tool': spec['tool'],
                    'output': produced[0],
                    'output_count': produced[1],
                }
        self.reachable = self._reachable_items()

    def _reachable_items(self) -> set[str]:
        reachable = set(item for item, count in self.initial.items() if count > 0)
        changed = True
        while changed:
            changed = False
            for source in self.sources.values():
                if source['tool'] is None or source['tool'] in reachable:
                    if source['output'] not in reachable:
                        reachable.add(source['output'])
                        changed = True
            for recipe in self.recipes:
                if recipe['station'] == 'furnace' and 'coal' not in reachable:
                    continue
                if all(any(option in reachable for option in slot) for slot in recipe['slots']):
                    if recipe['output'] not in reachable:
                        reachable.add(recipe['output'])
                        changed = True
        return reachable

    def _closure(self, targets: set[str]) -> tuple[set[str], list[dict[str, Any]], list[dict[str, Any]]]:
        needed = set(targets)
        queue = deque(targets)
        selected_recipes: dict[str, dict[str, Any]] = {}
        selected_sources: dict[str, dict[str, Any]] = {}
        while queue:
            item = queue.popleft()
            for recipe in self.by_output.get(item, []):
                filtered = [[option for option in slot if option in self.reachable]
                            for slot in recipe['slots']]
                if any(not slot for slot in filtered):
                    continue
                selected = {**recipe, 'slots': filtered}
                selected_recipes[recipe['id']] = selected
                for slot in filtered:
                    for option in slot:
                        if option not in needed:
                            needed.add(option)
                            queue.append(option)
            for block, source in self.sources.items():
                if source['output'] == item:
                    selected_sources[block] = source
                    tool = source['tool']
                    if tool and tool not in needed:
                        needed.add(tool)
                        queue.append(tool)
        # Coal is a consumed ingredient of every selected furnace route.
        if any(recipe['station'] == 'furnace' for recipe in selected_recipes.values()):
            if 'coal' not in needed:
                needed.add('coal')
                queue = deque(['coal'])
                while queue:
                    item = queue.popleft()
                    for block, source in self.sources.items():
                        if source['output'] == item:
                            selected_sources[block] = source
                            tool = source['tool']
                            if tool and tool not in needed:
                                needed.add(tool)
                                queue.append(tool)
        return needed, list(selected_recipes.values()), list(selected_sources.values())

    def plan(self, target: str, quantity: int = 1) -> PlanResult:
        target = item_id(target)
        if not isinstance(quantity, int) or quantity < 1:
            raise ValueError('quantity must be a positive integer')
        return self.plan_requirements({target: quantity}, target, quantity)

    def plan_requirements(self, requirements: dict[str, int], label: str = 'bundle',
                          quantity: int = 1) -> PlanResult:
        requirements = {item_id(item): count for item, count in requirements.items() if count}
        if not requirements or any(not isinstance(count, int) or count < 1
                                   for count in requirements.values()):
            raise ValueError('requirements must have positive integer quantities')
        target = label
        absent = [item for item in requirements if item not in self.names]
        if absent:
            return self._infeasible(target, quantity, 'required identifier absent', requirements)
        if all(self.initial[item] >= count for item, count in requirements.items()):
            return PlanResult(self.version, target, quantity, True, 0, [], {},
                              dict(self.initial), [], requirements=requirements)
        unreachable = [item for item in requirements if item not in self.reachable]
        if unreachable:
            return self._infeasible(target, quantity,
                                    'unreachable under executable mechanisms', requirements)
        items, recipes, sources = self._closure(set(requirements))

        problem = pulp.LpProblem(f'webcraft_{self.version}_{target}', pulp.LpMinimize)
        recipe_vars = {
            recipe['id']: pulp.LpVariable(f"r_{index}", 0, self.action_limit, cat='Integer')
            for index, recipe in enumerate(recipes)
        }
        assignment_vars = {}
        for recipe_index, recipe in enumerate(recipes):
            for slot_index, options in enumerate(recipe['slots']):
                slot_vars = []
                for option_index, option in enumerate(options):
                    variable = pulp.LpVariable(
                        f'z_{recipe_index}_{slot_index}_{option_index}',
                        0, self.action_limit, cat='Integer')
                    assignment_vars[(recipe['id'], slot_index, option)] = variable
                    slot_vars.append(variable)
                problem += pulp.lpSum(slot_vars) == recipe_vars[recipe['id']]

        mine_vars = {}
        mine_used = {}
        for source_index, source in enumerate(sources):
            cap = self.capacities[source['block']]
            mine_vars[source['block']] = pulp.LpVariable(
                f'm_{source_index}', 0, cap, cat='Integer')
            mine_used[source['block']] = pulp.LpVariable(
                f'mu_{source_index}', 0, 1, cat='Binary')
            problem += mine_vars[source['block']] <= cap * mine_used[source['block']]
            problem += mine_vars[source['block']] >= mine_used[source['block']]

        fuel_vars = {}
        for recipe_index, recipe in enumerate(recipes):
            if recipe['station'] != 'furnace':
                continue
            fuel = pulp.LpVariable(f'f_{recipe_index}', 0, self.action_limit, cat='Integer')
            fuel_vars[recipe['id']] = fuel
            runs = recipe_vars[recipe['id']]
            problem += 8 * fuel >= runs
            problem += 8 * fuel - runs <= 7

        produced_by_item = defaultdict(list)
        consumed_by_item = defaultdict(list)
        for recipe in recipes:
            runs = recipe_vars[recipe['id']]
            produced_by_item[recipe['output']].append(recipe['output_count'] * runs)
            for slot_index, options in enumerate(recipe['slots']):
                for option in options:
                    consumed_by_item[option].append(
                        assignment_vars[(recipe['id'], slot_index, option)])
            if recipe['station'] == 'furnace':
                consumed_by_item['coal'].append(fuel_vars[recipe['id']])
        for source in sources:
            produced_by_item[source['output']].append(
                source['output_count'] * mine_vars[source['block']])

        all_items = items | set(produced_by_item) | set(consumed_by_item)
        for item in all_items:
            balance = (self.initial[item] + pulp.lpSum(produced_by_item[item])
                       - pulp.lpSum(consumed_by_item[item]))
            problem += balance >= requirements.get(item, 0)

        for source in sources:
            if not source['tool']:
                continue
            tool = source['tool']
            available = self.initial[tool] + pulp.lpSum(produced_by_item[tool])
            problem += available >= mine_used[source['block']]

        primary = pulp.lpSum(recipe_vars.values()) + pulp.lpSum(mine_vars.values())
        # Integer primary dominates deterministic tie-breaking toward fewer total recipe uses.
        problem += 1000 * primary + pulp.lpSum(recipe_vars.values())
        solver = pulp.PULP_CBC_CMD(msg=False, threads=1, timeLimit=30)
        status = problem.solve(solver)
        if status != pulp.LpStatusOptimal:
            return self._infeasible(target, quantity, f'MILP status {pulp.LpStatus[status]}', requirements)

        primary_cost = int(round(pulp.value(primary)))
        if primary_cost > self.action_limit:
            return self._infeasible(target, quantity, 'minimum exceeds action limit', requirements)
        selected = self._selected_actions(recipes, sources, recipe_vars,
                                          assignment_vars, mine_vars, fuel_vars)
        scheduled = self._schedule(selected, requirements)
        if scheduled is None:
            return self._infeasible(target, quantity, 'optimal balance has no executable ordering', requirements)
        actions, inventory, demand, recipe_ids = scheduled
        actual_cost = sum(action['primary_cost'] for action in actions)
        if actual_cost != primary_cost:
            raise AssertionError((actual_cost, primary_cost))
        return PlanResult(self.version, target, quantity, True, primary_cost, actions,
                          dict(sorted(demand.items())),
                          {k: v for k, v in sorted(inventory.items()) if v},
                          sorted(set(recipe_ids)), requirements=requirements)

    def _selected_actions(self, recipes, sources, recipe_vars, assignment_vars,
                          mine_vars, fuel_vars):
        selected = {'mines': [], 'crafts': [], 'smelts': []}
        for source in sources:
            count = int(round(pulp.value(mine_vars[source['block']]) or 0))
            if count:
                selected['mines'].append({**source, 'remaining': count})
        for recipe in recipes:
            runs = int(round(pulp.value(recipe_vars[recipe['id']]) or 0))
            if not runs:
                continue
            slot_queues = []
            for slot_index, options in enumerate(recipe['slots']):
                queue = []
                for option in options:
                    count = int(round(pulp.value(
                        assignment_vars[(recipe['id'], slot_index, option)]) or 0))
                    queue.extend([option] * count)
                if len(queue) != runs:
                    raise AssertionError('ingredient assignment mismatch')
                slot_queues.append(queue)
            if recipe['station'] == 'crafting_grid':
                for run in range(runs):
                    choices = [queue[run] for queue in slot_queues]
                    selected['crafts'].append({
                        'recipe': recipe,
                        'choices': choices,
                        'layout': recipe_layout(recipe['raw'], choices),
                    })
            else:
                # A furnace recipe has one ingredient slot. Group equal selected inputs.
                grouped = Counter(slot_queues[0])
                expected_fuel = int(round(pulp.value(fuel_vars[recipe['id']]) or 0))
                actual_fuel = sum(math.ceil(count / 8) for count in grouped.values())
                if actual_fuel != expected_fuel:
                    # MILP grouped fuel can undercount mixed inputs; retain exact executable groups.
                    # The caller will reject the cost/balance mismatch rather than hide it.
                    return {'invalid_fuel_grouping': True}
                for input_item, count in grouped.items():
                    selected['smelts'].append({
                        'recipe': recipe,
                        'input': input_item,
                        'remaining': count,
                        'fuel_count': math.ceil(count / 8),
                    })
        return selected

    def _schedule(self, selected, requirements):
        if selected.get('invalid_fuel_grouping'):
            return None
        inventory = Counter(self.initial)
        demand = Counter()
        actions = []
        recipe_ids = []
        mines = copy.deepcopy(selected['mines'])
        crafts = copy.deepcopy(selected['crafts'])
        smelts = copy.deepcopy(selected['smelts'])
        ordinal = Counter()

        while mines or crafts or smelts:
            progressed = False
            for index, mine in list(enumerate(mines)):
                tool = mine['tool']
                if tool and inventory[tool] < 1:
                    continue
                ordinal[mine['block']] += 1
                inventory[mine['output']] += mine['output_count']
                demand[mine['block']] += 1
                actions.append({
                    'method': 'mine',
                    'node_type': mine['block'],
                    'node_ordinal': ordinal[mine['block']],
                    'requires_tool': tool,
                    'expected': {'name': mine['output'], 'count': mine['output_count']},
                    'primary_cost': 1,
                })
                mine['remaining'] -= 1
                if mine['remaining'] == 0:
                    mines.pop(index)
                progressed = True
                break
            if progressed:
                continue
            for index, craft in list(enumerate(crafts)):
                needed = Counter(craft['choices'])
                if any(inventory[item] < count for item, count in needed.items()):
                    continue
                inventory.subtract(needed)
                recipe = craft['recipe']
                inventory[recipe['output']] += recipe['output_count']
                actions.append({
                    'method': 'craft_grid',
                    'layout': craft['layout'],
                    'recipe_id': recipe['id'],
                    'expected': {'name': recipe['output'], 'count': recipe['output_count']},
                    'primary_cost': 1,
                })
                recipe_ids.append(recipe['id'])
                crafts.pop(index)
                progressed = True
                break
            if progressed:
                continue
            for index, smelt in list(enumerate(smelts)):
                count = smelt['remaining']
                if inventory[smelt['input']] < count or inventory['coal'] < smelt['fuel_count']:
                    continue
                inventory[smelt['input']] -= count
                inventory['coal'] -= smelt['fuel_count']
                recipe = smelt['recipe']
                inventory[recipe['output']] += recipe['output_count'] * count
                actions.append({
                    'method': 'smelt',
                    'input': smelt['input'],
                    'fuel': 'coal',
                    'n': count,
                    'fuel_count': smelt['fuel_count'],
                    'recipe_id': recipe['id'],
                    'expected': {'name': recipe['output'],
                                 'count': recipe['output_count'] * count},
                    'primary_cost': count,
                })
                recipe_ids.append(recipe['id'])
                smelts.pop(index)
                progressed = True
                break
            if not progressed:
                return None
        if any(inventory[item] < count for item, count in requirements.items()):
            return None
        return actions, inventory, demand, recipe_ids

    def _infeasible(self, target, quantity, reason, requirements=None):
        return PlanResult(self.version, target, quantity, False, None, [], {},
                          dict(self.initial), [], reason, requirements)


def validate_plan(result: PlanResult, initial: Counter | None = None) -> None:
    if not result.feasible:
        return
    inventory = Counter(initial if initial is not None else INITIAL)
    used = Counter()
    cost = 0
    for action in result.actions:
        method = action['method']
        if method == 'mine':
            tool = action['requires_tool']
            if tool and inventory[tool] < 1:
                raise AssertionError(f'missing mining tool {tool}')
            used[action['node_type']] += 1
            expected = action['expected']
            inventory[expected['name']] += expected['count']
        elif method == 'craft_grid':
            needed = Counter(item for item in action['layout'] if item)
            if any(inventory[item] < count for item, count in needed.items()):
                raise AssertionError(f'missing craft ingredient for {action["recipe_id"]}')
            inventory.subtract(needed)
            expected = action['expected']
            inventory[expected['name']] += expected['count']
        elif method == 'smelt':
            if inventory[action['input']] < action['n']:
                raise AssertionError('missing smelting input')
            if inventory[action['fuel']] < action['fuel_count']:
                raise AssertionError('missing smelting fuel')
            inventory[action['input']] -= action['n']
            inventory[action['fuel']] -= action['fuel_count']
            expected = action['expected']
            inventory[expected['name']] += expected['count']
        else:
            raise AssertionError(f'unsupported action {method}')
        cost += action['primary_cost']
        if any(value < 0 for value in inventory.values()):
            raise AssertionError('negative inventory')
    requirements = result.requirements or {result.target: result.quantity}
    if any(inventory[item] < count for item, count in requirements.items()):
        raise AssertionError('requirements not produced')
    if cost != result.primary_cost:
        raise AssertionError('primary cost mismatch')
    if dict(sorted(used.items())) != result.resource_demand:
        raise AssertionError('resource demand mismatch')
