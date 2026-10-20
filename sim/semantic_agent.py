"""Truth-table state machine behind the frozen WebCraft semantic interface.

This module intentionally models only the semantic interface.  It does not model
coordinates, pathfinding, entities, container windows, or Minecraft tick timing.
"""
from __future__ import annotations

import hashlib
import importlib
import json
import re
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

from oracle.planner import INITIAL, SOURCE_SPECS, guaranteed_source, ingredient_slots


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ('1.16.5', '1.19', '1.21.11')
PUBLIC_METHODS = ('inventory', 'mine', 'craft_grid', 'smelt', 'check_name',
                  'report_version', 'submit', 'search')
SECRET_KEYS = re.compile(
    r'^(?:x|y|z|position|coordinates?|height|world.*|dimension.*|time_?ticks?|'
    r'entity_?id|protocol.*|registry.*|version|server_brand)$', re.I)
VALID_NAME = re.compile(r'^(?:minecraft:)?[a-z_]+$')
TOOL_DURABILITY = {
    # Extracted from harness/node_modules/minecraft-data for all three frozen versions.
    'wooden_pickaxe': 59, 'stone_pickaxe': 131, 'iron_pickaxe': 250,
    'diamond_pickaxe': 1561, 'golden_pickaxe': 32, 'netherite_pickaxe': 2031,
}
TOOL_LEVEL = {
    'wooden_pickaxe': 0, 'golden_pickaxe': 0, 'stone_pickaxe': 1,
    'iron_pickaxe': 2, 'diamond_pickaxe': 3, 'netherite_pickaxe': 4,
}
REQUIRED_TOOL_LEVEL = {None: -1, 'wooden_pickaxe': 0, 'stone_pickaxe': 1,
                       'iron_pickaxe': 2}
FUEL_BURN = {'coal': 8}
_SESSIONS: dict[str, '_Core'] = {}
_NEXT_SESSION = 0
_FIXTURES: dict[str, tuple[dict[str, Any], dict[str, Any], dict[str, Any], set[str], tuple['_Recipe', ...]]] = {}


class SemanticError(Exception):
    """An error whose message is already part of the public contract."""


def _item_id(value: str | None) -> str | None:
    return value.removeprefix('minecraft:') if isinstance(value, str) else value


def _public_value(value: Any) -> Any:
    if isinstance(value, str):
        value = re.sub(r'\b(?:1\.16\.5|1\.19(?:\.\d+)?|1\.21\.11)\b', '[redacted]', value)
        return re.sub(r'\b(?:x|y|z|height|world_boundary|entity_id|dimension_height|time_ticks)\s*[:=]\s*-?\d+(?:\.\d+)?',
                      '[redacted]', value, flags=re.I)
    if isinstance(value, list):
        return [_public_value(item) for item in value]
    if isinstance(value, dict):
        return {key: _public_value(item) for key, item in value.items()
                if not SECRET_KEYS.match(str(key))}
    return value


def _safe_error(error: Exception) -> str:
    message = str(error)
    if message.startswith('missing ingredient:'):
        return 'missing ingredient'
    allowed = {
        'unknown resource node', 'resource node is empty',
        'layout must contain exactly 9 cells', 'crafting table unavailable',
        'furnace unavailable', 'missing smelting input or fuel', 'smelting timeout',
        'n must be a positive integer', 'episode closed', 'invalid item identifier',
    }
    return message if message in allowed else 'action could not be completed'


def _primary_cost(method: str, params: dict[str, Any]) -> int:
    if method == 'smelt':
        value = params.get('n')
        return value if isinstance(value, int) and not isinstance(value, bool) and value > 0 else 1
    return 0 if method in ('inventory', 'report_version', 'submit', 'search') else 1


def _flow(before: Counter, after: Counter) -> dict[str, list[dict[str, Any]]]:
    consumed, produced = [], []
    for name in sorted(set(before) | set(after)):
        delta = after[name] - before[name]
        if delta < 0:
            consumed.append({'name': name, 'count': -delta})
        elif delta > 0:
            produced.append({'name': name, 'count': delta})
    return {'consumed': consumed, 'produced': produced}


def _trim_layout(layout: list[str | None]) -> tuple[tuple[str | None, ...], ...]:
    rows = [layout[index:index + 3] for index in range(0, 9, 3)]
    while rows and all(cell is None for cell in rows[0]):
        rows.pop(0)
    while rows and all(cell is None for cell in rows[-1]):
        rows.pop()
    if not rows:
        return tuple()
    left = min(index for row in rows for index, cell in enumerate(row) if cell is not None)
    right = max(index for row in rows for index, cell in enumerate(row) if cell is not None)
    return tuple(tuple(row[left:right + 1]) for row in rows)


def _recipe_shape(raw: dict[str, Any], mirror: bool = False) -> tuple[tuple[tuple[str, ...] | None, ...], ...]:
    pattern = raw.get('pattern', [])
    width = max((len(row) for row in pattern), default=0)
    rows: list[list[tuple[str, ...] | None]] = []
    for row in pattern:
        cells = [tuple(_item_id(item) for item in raw['key'].get(symbol, []))
                 if symbol != ' ' else None for symbol in row]
        cells.extend([None] * (width - len(cells)))
        rows.append(list(reversed(cells)) if mirror else cells)
    while rows and all(cell is None for cell in rows[0]):
        rows.pop(0)
    while rows and all(cell is None for cell in rows[-1]):
        rows.pop()
    if not rows:
        return tuple()
    left = min(index for row in rows for index, cell in enumerate(row) if cell is not None)
    right = max(index for row in rows for index, cell in enumerate(row) if cell is not None)
    return tuple(tuple(row[left:right + 1]) for row in rows)


@dataclass(frozen=True)
class _Recipe:
    recipe_id: str
    output: str
    output_count: int
    station: str
    ordered: bool
    slots: tuple[tuple[str, ...], ...]
    shape: tuple[tuple[tuple[str, ...] | None, ...], ...] = tuple()
    mirrored_shape: tuple[tuple[tuple[str, ...] | None, ...], ...] = tuple()


class _Core:
    def __init__(self, version: str, log_path: str | Path | None, *,
                 initial_inventory: Counter | dict[str, int] | None = None,
                 search_condition: str = 'matched', search_index_dir: str = ''):
        if version not in VERSIONS:
            raise ValueError('unsupported version')
        self.version = version
        self.log_path = Path(log_path) if log_path else None
        if self.log_path:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            self.log_path.write_text('')
        fixture = _FIXTURES.get(version)
        if fixture is None:
            truth = json.loads((ROOT / f'truth/{version}.json').read_text())
            arena = json.loads((ROOT / 'harness/config/arena.json').read_text())
            policy = json.loads((ROOT / 'config/recipe_type_policy.json').read_text())
            self.truth, self.arena, self.policy = truth, arena, policy
            fixture = (truth, arena, policy,
                       {_item_id(value) for value in truth['names']['items']},
                       tuple(self._recipes()))
            _FIXTURES[version] = fixture
        self.truth, self.arena, self.policy, self.names, self.recipes = fixture
        self.inventory = Counter(initial_inventory if initial_inventory is not None else INITIAL)
        self.durability: dict[str, list[int]] = {
            name: [TOOL_DURABILITY[name]] * count for name, count in self.inventory.items()
            if name in TOOL_DURABILITY and count > 0
        }
        self.nodes = {row['nodeId']: row['block'] for row in self.arena['resources']}
        self.depleted: set[str] = set()
        self.finished = False
        self.sequence = 0
        self.search_condition = search_condition
        self.search_index_dir = search_index_dir

    def _recipes(self) -> list[_Recipe]:
        executable = set(self.policy['executable_recipe_types'])
        recipes = []
        for raw in self.truth['recipes']:
            recipe_type = _item_id(raw.get('type'))
            if recipe_type not in executable:
                continue
            station = raw.get('station')
            if station not in ('crafting_grid', 'furnace'):
                continue
            slots = tuple(tuple(_item_id(item) for item in options)
                          for options in ingredient_slots(raw))
            if not slots or any(not options for options in slots):
                continue
            output = _item_id(raw['result'].get('item'))
            if not output:
                continue
            ordered = bool(raw.get('ordered'))
            shape = _recipe_shape(raw) if ordered else tuple()
            recipes.append(_Recipe(raw['id'], output, int(raw['result'].get('count', 1)),
                                   station, ordered, slots, shape,
                                   _recipe_shape(raw, mirror=True) if ordered else tuple()))
        return recipes

    def _snapshot(self) -> list[dict[str, Any]]:
        return [{'name': name, 'count': self.inventory[name]}
                for name in sorted(self.inventory) if self.inventory[name] > 0]

    def _add(self, name: str, count: int) -> None:
        self.inventory[name] += count
        if name in TOOL_DURABILITY:
            self.durability.setdefault(name, []).extend([TOOL_DURABILITY[name]] * count)

    def _remove(self, name: str, count: int) -> None:
        if self.inventory[name] < count:
            raise SemanticError(f'missing ingredient: {name}')
        self.inventory[name] -= count
        if self.inventory[name] == 0:
            self.inventory.pop(name, None)
        if name in self.durability:
            for _ in range(count):
                self.durability[name].pop(0)
            if not self.durability[name]:
                self.durability.pop(name)

    def _use_tool(self, required: str | None) -> bool:
        needed = REQUIRED_TOOL_LEVEL[required]
        candidates = sorted((name for name, values in self.durability.items()
                             if values and TOOL_LEVEL.get(name, -1) >= needed),
                            key=lambda name: (TOOL_LEVEL[name], name))
        if required is not None and not candidates:
            return False
        if not candidates:
            return True
        name = candidates[0]
        self.durability[name][0] -= 1
        if self.durability[name][0] == 0:
            self.durability[name].pop(0)
            self.inventory[name] -= 1
            if self.inventory[name] == 0:
                self.inventory.pop(name)
            if not self.durability[name]:
                self.durability.pop(name)
        return True

    def _invoke(self, method: str, params: dict[str, Any], operation: Callable[[], Any],
                *, intent: Any = None) -> Any:
        if self.finished:
            raise SemanticError('episode closed')
        before = Counter(self.inventory)
        try:
            result = operation()
        except Exception as error:  # Convert all private implementation details at the boundary.
            result = {'success': False, 'error': _safe_error(error)}
        flow = _flow(before, self.inventory)
        cost = {'primary': _primary_cost(method, params), 'wall_ms': 0,
                'net_displacement': 'not_applicable', 'consumed_items': flow['consumed'],
                'model_calls': 0}
        if isinstance(result, dict):
            result = _public_value(result)
            result['cost'] = cost
        self.sequence += 1
        record = {'timestamp': f'1970-01-01T00:00:{self.sequence:02d}.000Z',
                  'schema_version': 2, 'sequence': self.sequence, 'method': method,
                  'params': _public_value(params), 'result': result, 'cost': cost,
                  'item_flow': flow,
                  'success': result.get('success', True) is not False if isinstance(result, dict) else True}
        if intent is not None:
            # Agent-declared metadata: the evaluator consumes but does not validate it.
            record['intent'] = _public_value(intent)
        if self.log_path:
            with self.log_path.open('a', encoding='utf8') as handle:
                handle.write(json.dumps(record, sort_keys=True, separators=(',', ':')) + '\n')
        return result

    def inventory_api(self, *, intent: Any = None) -> Any:
        return self._invoke('inventory', {}, self._snapshot, intent=intent)

    def mine_api(self, node_id: str, *, intent: Any = None) -> Any:
        def operation():
            block = self.nodes.get(node_id)
            if block is None:
                raise SemanticError('unknown resource node')
            if node_id in self.depleted:
                raise SemanticError('resource node is empty')
            spec = SOURCE_SPECS[block]
            if not self._use_tool(spec['tool']):
                raise SemanticError('mining tool is insufficient')
            produced = guaranteed_source(self.version, block)
            self.depleted.add(node_id)
            if produced is None:
                return {'success': True, 'items': [],
                        'cost': {'environment_steps': 1, 'travel_distance': 'not_applicable'}}
            name, count = produced
            self._add(name, count)
            return {'success': True, 'items': [{'name': name, 'count': count}],
                    'cost': {'environment_steps': 1, 'travel_distance': 'not_applicable'}}
        return self._invoke('mine', {'nodeId': node_id}, operation, intent=intent)

    @staticmethod
    def _layout_cells(layout: Any) -> list[str | None]:
        if not isinstance(layout, list) or len(layout) != 9:
            raise SemanticError('layout must contain exactly 9 cells')
        cells: list[str | None] = []
        for value in layout:
            if value is None:
                cells.append(None)
            elif isinstance(value, str):
                cells.append(_item_id(value))
            elif isinstance(value, dict) and isinstance(value.get('name'), str) and value.get('count', 1) == 1:
                cells.append(_item_id(value['name']))
            else:
                cells.append('__invalid__')
        return cells

    def _match_recipe(self, cells: list[str | None]) -> _Recipe | None:
        provided = _trim_layout(cells)
        provided_counts = Counter(value for value in cells if value)
        for recipe in self.recipes:
            if recipe.station != 'crafting_grid':
                continue
            if recipe.ordered:
                for expected in (recipe.shape, recipe.mirrored_shape):
                    if len(provided) != len(expected) or any(len(left) != len(right)
                                                              for left, right in zip(provided, expected)):
                        continue
                    if all((actual is None if options is None else actual in options)
                           for actual_row, expected_row in zip(provided, expected)
                           for actual, options in zip(actual_row, expected_row)):
                        return recipe
            elif len(provided_counts) or recipe.slots:
                remaining = list(recipe.slots)
                for name in sorted(provided_counts.elements()):
                    index = next((i for i, options in enumerate(remaining) if name in options), None)
                    if index is None:
                        break
                    remaining.pop(index)
                else:
                    if not remaining and sum(provided_counts.values()) == len(recipe.slots):
                        return recipe
        return None

    def craft_api(self, layout: Any, *, intent: Any = None) -> Any:
        def operation():
            cells = self._layout_cells(layout)
            counts = Counter(value for value in cells if value)
            for name, count in counts.items():
                if self.inventory[name] < count:
                    raise SemanticError(f'missing ingredient: {name}')
            recipe = self._match_recipe(cells)
            if recipe is None:
                return {'success': False, 'product': None,
                        'cost': {'environment_steps': 0}}
            for name, count in counts.items():
                self._remove(name, count)
            self._add(recipe.output, recipe.output_count)
            return {'success': True,
                    'product': {'name': recipe.output, 'count': recipe.output_count},
                    'cost': {'environment_steps': 1}}
        return self._invoke('craft_grid', {'layout': layout}, operation, intent=intent)

    def smelt_api(self, input_item: str, fuel: str, n: Any, *, intent: Any = None) -> Any:
        def operation():
            if not isinstance(n, int) or isinstance(n, bool) or n < 1:
                raise SemanticError('n must be a positive integer')
            input_name, fuel_name = _item_id(input_item), _item_id(fuel)
            if self.inventory[input_name] < n or self.inventory[fuel_name] < 1:
                raise SemanticError('missing smelting input or fuel')
            recipe = next((entry for entry in self.recipes
                           if entry.station == 'furnace' and input_name in entry.slots[0]), None)
            if recipe is None or fuel_name not in FUEL_BURN:
                raise SemanticError('smelting timeout')
            fuel_count = (n + FUEL_BURN[fuel_name] - 1) // FUEL_BURN[fuel_name]
            if self.inventory[fuel_name] < fuel_count:
                raise SemanticError('missing smelting input or fuel')
            self._remove(input_name, n)
            self._remove(fuel_name, fuel_count)
            self._add(recipe.output, recipe.output_count * n)
            return {'success': True,
                    'items': [{'name': recipe.output, 'count': recipe.output_count * n}],
                    'cost': {'environment_steps': n}}
        return self._invoke('smelt', {'input': input_item, 'fuel': fuel, 'n': n}, operation,
                            intent=intent)

    def check_name_api(self, name: str, *, intent: Any = None) -> Any:
        def operation():
            if not isinstance(name, str) or not VALID_NAME.fullmatch(name):
                raise SemanticError('invalid item identifier')
            return {'resolvable': _item_id(name) in self.names}
        return self._invoke('check_name', {'name': name}, operation, intent=intent)

    def report_version_api(self, guess: Any, *, intent: Any = None) -> Any:
        digest = hashlib.sha256(str(guess).encode()).hexdigest()
        return self._invoke('report_version', {'guess_sha256': digest},
                            lambda: {'confirmed': True}, intent=intent)

    def submit_api(self, result: Any, *, intent: Any = None) -> Any:
        return self._invoke('submit', {'result': _public_value(result)},
                            lambda: {'confirmed': True}, intent=intent)

    def search_api(self, query: str, k: int, *, intent: Any = None) -> Any:
        def operation():
            try:
                module = importlib.import_module('corpus.search_backend')
                backend = module.SearchBackend(self.search_condition, self.search_index_dir)
                rows = backend.search(query, k)
            except ModuleNotFoundError:
                rows = []
            allowed = ('text', 'source_site', 'page_title', 'revid', 'timestamp', 'chunk_id')
            return [{key: row[key] for key in allowed if key in row} for row in rows]
        return self._invoke('search', {'q': query, 'k': k}, operation, intent=intent)

    def finish(self) -> None:
        self.finished = True


class _ApiFacade:
    """The agent-visible facade has no direct reference to truth or evaluator state."""
    __slots__ = ('_token',)

    def __init__(self, token: str):
        self._token = token

    def inventory(self, *, intent=None): return _SESSIONS[self._token].inventory_api(intent=intent)
    def mine(self, node_id, *, intent=None): return _SESSIONS[self._token].mine_api(node_id, intent=intent)
    def craft_grid(self, layout, *, intent=None): return _SESSIONS[self._token].craft_api(layout, intent=intent)
    def smelt(self, input_item, fuel, n, *, intent=None): return _SESSIONS[self._token].smelt_api(input_item, fuel, n, intent=intent)
    def check_name(self, name, *, intent=None): return _SESSIONS[self._token].check_name_api(name, intent=intent)
    def report_version(self, guess, *, intent=None): return _SESSIONS[self._token].report_version_api(guess, intent=intent)
    def submit(self, result, *, intent=None): return _SESSIONS[self._token].submit_api(result, intent=intent)
    def search(self, query, k, *, intent=None): return _SESSIONS[self._token].search_api(query, k, intent=intent)


class _Evaluator:
    def __init__(self, token: str, answer_provider: Callable[[Any], list[Any]] | None):
        self._token = token
        self._answer_provider = answer_provider
        self._finished = False

    def finish_episode(self) -> None:
        _SESSIONS[self._token].finish()
        self._finished = True

    def answer_claims(self, claims: list[Any]) -> list[Any]:
        if not self._finished:
            raise SemanticError('evaluation requires a finished episode')
        if self._answer_provider is None:
            raise SemanticError('agent answer provider is not configured')
        answers = self._answer_provider(json.loads(json.dumps(claims)))
        if (not isinstance(answers, list) or len(answers) != len(claims)
                or any(value not in (True, False, 'unknown') for value in answers)):
            raise SemanticError('invalid claim answers')
        return answers


def create_simulated_agent(*, version: str, log_path: str | Path | None = None,
                           answer_claims: Callable[[Any], list[Any]] | None = None,
                           initial_inventory: Counter | dict[str, int] | None = None,
                           search_condition: str = 'matched', search_index_dir: str = '') -> dict[str, Any]:
    """Return separate agent-visible API and evaluator capabilities."""
    global _NEXT_SESSION
    _NEXT_SESSION += 1
    token = f'sim-{_NEXT_SESSION:016x}'
    _SESSIONS[token] = _Core(version, log_path, initial_inventory=initial_inventory,
                             search_condition=search_condition, search_index_dir=search_index_dir)
    api = _ApiFacade(token)
    evaluator = _Evaluator(token, answer_claims)
    return {'api': api, 'evaluator': evaluator,
            'shutdown': lambda: _SESSIONS.pop(token, None)}
