import hashlib
import json
import tempfile
import unittest
from collections import Counter
from pathlib import Path

from oracle.planner import SOURCE_SPECS, guaranteed_source, ingredient_slots, recipe_layout
from sim.semantic_agent import (PUBLIC_METHODS, TOOL_DURABILITY, _ApiFacade, _SESSIONS,
                                create_simulated_agent)


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ('1.16.5', '1.19', '1.21.11')


def recipe_choices(recipe):
    return [options[0].removeprefix('minecraft:') for options in ingredient_slots(recipe)]


class SimulatorTests(unittest.TestCase):
    def make(self, version, **kwargs):
        return create_simulated_agent(version=version, **kwargs)

    def test_every_frozen_executable_recipe(self):
        policy = json.loads((ROOT / 'config/recipe_type_policy.json').read_text())
        executable = set(policy['executable_recipe_types'])
        for version in VERSIONS:
            truth = json.loads((ROOT / f'truth/{version}.json').read_text())
            for recipe in truth['recipes']:
                if recipe['type'].removeprefix('minecraft:') not in executable:
                    continue
                if recipe['station'] not in ('crafting_grid', 'furnace'):
                    continue
                choices = recipe_choices(recipe)
                if not choices:
                    continue
                inventory = Counter(choices)
                if recipe['station'] == 'crafting_grid':
                    if recipe.get('ordered'):
                        layout = recipe_layout(recipe, choices)
                    else:
                        layout = choices + [None] * (9 - len(choices))
                    handle = self.make(version, initial_inventory=inventory)
                    result = handle['api'].craft_grid(layout)
                else:
                    inventory['coal'] = max(inventory['coal'], 1)
                    handle = self.make(version, initial_inventory=inventory)
                    result = handle['api'].smelt(choices[0], 'coal', 1)
                self.assertTrue(result['success'], (version, recipe['id'], result))
                product = result.get('product') or result.get('items', [None])[0]
                self.assertEqual(product, {'name': recipe['result']['item'].removeprefix('minecraft:'),
                                           'count': int(recipe['result'].get('count', 1))})
                handle['shutdown']()

    def test_every_arena_node_and_wrong_tool_cost(self):
        arena = json.loads((ROOT / 'harness/config/arena.json').read_text())
        for version in VERSIONS:
            for node in arena['resources']:
                spec = SOURCE_SPECS[node['block']]
                required = spec['tool']
                inventory = Counter({required: 1}) if required else Counter()
                handle = self.make(version, initial_inventory=inventory)
                result = handle['api'].mine(node['nodeId'])
                expected = guaranteed_source(version, node['block'])
                if expected is None:
                    self.assertTrue(result['success'])
                    self.assertEqual(result['items'], [])
                else:
                    self.assertTrue(result['success'], (version, node, result))
                    self.assertEqual(result['items'], [{'name': expected[0], 'count': expected[1]}])
                handle['shutdown']()
            target = next(node for node in arena['resources'] if SOURCE_SPECS[node['block']]['tool'])
            handle = self.make(version, initial_inventory=Counter())
            result = handle['api'].mine(target['nodeId'])
            self.assertFalse(result['success'])
            self.assertEqual(result['cost']['primary'], 1)
            self.assertEqual(result['error'], 'action could not be completed')
            handle['shutdown']()

    def test_separator_vectors_and_public_facade(self):
        expectations = json.loads((ROOT / 'truth/separator_truth.json').read_text())['versions']
        layouts = {
            2: ['cobbled_deepslate'] * 3 + [None, 'stick', None, None, 'stick', None],
            5: ['string', None, None, 'leather', None, None, None, None, None],
            6: ['chiseled_stone_bricks'] * 4 + ['iron_ingot'] + ['chiseled_stone_bricks'] * 4,
            7: ['chiseled_stone_bricks'] * 4 + ['netherite_ingot'] + ['chiseled_stone_bricks'] * 4,
            8: [None, 'leather', None, 'leather', 'iron_ingot', 'leather', None, None, None],
            9: ['copper_ingot'] * 3 + [None, 'stick', None, None, 'stick', None],
            12: ['paper', 'iron_nugget', None, None, None, None, None, None, None],
        }
        names = {3: 'scute', 4: 'turtle_scute', 10: 'chain', 11: 'iron_chain'}
        for version in VERSIONS:
            expected = {row['id']: row['expected'] for row in expectations[version]}
            handle = self.make(version, initial_inventory=Counter({'stone_pickaxe': 1}))
            mine = handle['api'].mine('iron_1')
            self.assertTrue(mine['success'])
            self.assertEqual(any(item['name'] == 'raw_iron' for item in mine['items']), expected[1])
            handle['shutdown']()
            for identifier, name in names.items():
                handle = self.make(version)
                self.assertEqual(handle['api'].check_name(name)['resolvable'], expected[identifier])
                handle['shutdown']()
            for identifier, layout in layouts.items():
                handle = self.make(version, initial_inventory=Counter(cell for cell in layout if cell))
                result = handle['api'].craft_grid(layout)
                self.assertEqual(result['success'], expected[identifier],
                                 (version, identifier, result))
                handle['shutdown']()
        self.assertEqual(_ApiFacade.__slots__, ('_token',))
        self.assertEqual(sorted(name for name in dir(_ApiFacade) if not name.startswith('_')),
                         sorted(PUBLIC_METHODS))
        self.assertFalse(hasattr(self.make('1.19')['api'], '__dict__'))

    def test_deterministic_logs_and_durability(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / 'first.jsonl', Path(directory) / 'second.jsonl'
            for path in (first, second):
                handle = self.make('1.19', log_path=path,
                                   initial_inventory=Counter({'stone_pickaxe': 1}))
                self.assertTrue(handle['api'].mine('iron_1')['success'])
                self.assertEqual(handle['api'].inventory()[0]['name'], 'raw_iron')
                handle['shutdown']()
            self.assertEqual(first.read_bytes(), second.read_bytes())
        handle = self.make('1.19', initial_inventory=Counter({'wooden_pickaxe': 1}))
        core = _SESSIONS[handle['api']._token]
        for number in range(TOOL_DURABILITY['wooden_pickaxe'] + 1):
            core.nodes[f'test_stone_{number}'] = 'stone'
        for number in range(TOOL_DURABILITY['wooden_pickaxe']):
            self.assertTrue(handle['api'].mine(f'test_stone_{number}')['success'])
        self.assertFalse(handle['api'].mine(f'test_stone_{TOOL_DURABILITY["wooden_pickaxe"]}')['success'])
        handle['shutdown']()

    def test_agent_declared_intent_is_logged_but_not_returned(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'intent.jsonl'
            handle = self.make('1.19', log_path=path,
                               initial_inventory=Counter({'stone_pickaxe': 1}))
            result = handle['api'].mine('iron_1', intent='probe')
            handle['shutdown']()
            self.assertNotIn('intent', result)
            record = json.loads(path.read_text())
            self.assertEqual(record['intent'], 'probe')
            self.assertEqual(record['cost']['primary'], 1)

    def test_mirror_and_fuel_net_consumption(self):
        # A wooden axe is asymmetric, so its reflected shaped layout exercises mirror matching.
        handle = self.make('1.19', initial_inventory=Counter({'oak_planks': 3, 'stick': 2}))
        result = handle['api'].craft_grid([None, 'oak_planks', 'oak_planks',
                                           None, 'stick', 'oak_planks',
                                           None, 'stick', None])
        self.assertTrue(result['success'])
        self.assertEqual(result['product']['name'], 'wooden_axe')
        handle['shutdown']()
        handle = self.make('1.19', initial_inventory=Counter({'raw_iron': 9, 'coal': 2}))
        result = handle['api'].smelt('raw_iron', 'coal', 9)
        self.assertTrue(result['success'])
        self.assertEqual(result['items'], [{'name': 'iron_ingot', 'count': 9}])
        self.assertEqual(handle['api'].inventory(), [{'name': 'iron_ingot', 'count': 9}])
        handle['shutdown']()
