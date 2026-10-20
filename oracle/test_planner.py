import json
import unittest
from collections import Counter

from oracle.planner import (INITIAL, OraclePlanner, SOURCE_SPECS,
                            guaranteed_source, item_id, validate_plan)


VERSIONS = ('1.16.5', '1.19', '1.21.11')
COMMON_TARGETS = (
    'wooden_pickaxe', 'stone_pickaxe', 'furnace', 'iron_ingot',
    'iron_pickaxe', 'bucket', 'compass', 'shears', 'shield', 'rail',
    'minecart', 'cauldron', 'hopper', 'iron_door', 'iron_bars',
    'redstone_torch', 'piston', 'clock', 'gold_ingot', 'diamond_pickaxe',
)


class PlannerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.planners = {version: OraclePlanner(version) for version in VERSIONS}

    def test_twenty_common_items_are_executable_in_each_world(self):
        records = []
        for version, planner in self.planners.items():
            for target in COMMON_TARGETS:
                with self.subTest(version=version, target=target):
                    result = planner.plan(target)
                    self.assertTrue(result.feasible, result.reason)
                    self.assertLessEqual(result.primary_cost, 120)
                    validate_plan(result)
                    records.append({'version': version, 'target': target,
                                    'primary_cost': result.primary_cost,
                                    'resource_demand': result.resource_demand})
        self.assertEqual(len(records), len(VERSIONS) * len(COMMON_TARGETS))

    def test_missing_resources_are_infeasible(self):
        planner = OraclePlanner('1.21.11', capacities={block: 0 for block in SOURCE_SPECS})
        result = planner.plan('iron_pickaxe')
        self.assertFalse(result.feasible)

    def test_version_dependent_iron_route(self):
        old = self.planners['1.16.5'].plan('iron_ingot')
        new = self.planners['1.21.11'].plan('iron_ingot')
        self.assertIn('iron_ore', [a.get('input') for a in old.actions])
        self.assertIn('raw_iron', [a.get('input') for a in new.actions])

    def test_probabilistic_gravel_identity_is_not_a_leaf(self):
        self.assertIsNone(guaranteed_source('1.21.11', 'gravel'))
        planner = self.planners['1.21.11']
        self.assertFalse(planner.plan('flint').feasible)

    def test_guaranteed_yields_match_extracted_possible_sets(self):
        for version in VERSIONS:
            with open(f'truth/{version}.json') as handle:
                truth = json.load(handle)
            drops = {item_id(row['block']): row for row in truth['drops']}
            for block in SOURCE_SPECS:
                guaranteed = guaranteed_source(version, block)
                if guaranteed:
                    self.assertIn('minecraft:' + guaranteed[0],
                                  drops[block]['no_silk_touch_items'])
            self.assertGreaterEqual(guaranteed_source(version, 'redstone_ore')[1], 4)
            self.assertGreaterEqual(guaranteed_source(version, 'lapis_ore')[1], 4)

    def test_initial_target_costs_zero(self):
        result = self.planners['1.21.11'].plan('stick', 2)
        self.assertTrue(result.feasible)
        self.assertEqual(result.primary_cost, 0)

    def test_joint_requirements_share_intermediates(self):
        planner = self.planners['1.21.11']
        result = planner.plan_requirements({'iron_ingot': 4, 'redstone': 1})
        self.assertTrue(result.feasible, result.reason)
        validate_plan(result)
        self.assertGreaterEqual(result.final_inventory['iron_ingot'], 4)
        self.assertGreaterEqual(result.final_inventory['redstone'], 1)


if __name__ == '__main__':
    unittest.main()
