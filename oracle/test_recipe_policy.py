import csv
import json
import sys
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))

from recipe_type_policy import (EXECUTABLE_TYPES, dry_run_params, execution_route,
                                rejection_reason)  # noqa: E402


class RecipeTypePolicyTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = [json.loads(line) for line in
                    (ROOT / 'data/drafts/conflicts.jsonl').read_text().splitlines() if line]
        with (ROOT / 'reports/non_grid_recipe_53_review.csv').open(newline='') as handle:
            cls.details = list(csv.DictReader(handle))

    def test_non_whitelist_types_never_enter_execution_route(self):
        rejected = []
        for row in self.rows:
            assertion = row.get('assertion', {})
            if assertion.get('kind') != 'recipe':
                continue
            recipe_type = assertion.get('recipe_type')
            reason = rejection_reason(recipe_type)
            if reason:
                rejected.append(row['id'])
                self.assertIsNone(execution_route(recipe_type))
                source, reported_type = reason.split(':', 1)
                self.assertIn(source, {'non_grid_recipe_type',
                                       'unsupported_semantic_interface'})
                self.assertEqual(reported_type, recipe_type)
        self.assertTrue(rejected)

    def test_c_count_matches_fifty_three_row_detail(self):
        self.assertEqual(len(self.details), 53)
        c_rows = [row for row in self.details if row['proposed_grade'] == 'C']
        self.assertEqual(len(c_rows), 53)
        self.assertEqual(Counter(row['type'] for row in c_rows),
                         Counter({'crafting_transmute': 32, 'smithing': 9,
                                  'smithing_transform': 12}))
        by_version = {
            version: sum(row[f'true_{version}'] == 'True' for row in c_rows)
            for version in ('1.16.5', '1.19', '1.21.11')
        }
        self.assertEqual(by_version, {'1.16.5': 9, '1.19': 9, '1.21.11': 44})

    def test_all_executable_recipes_have_valid_interface_dry_run(self):
        counts = Counter()
        for version in ('1.16.5', '1.19', '1.21.11'):
            truth = json.loads((ROOT / f'truth/{version}.json').read_text())
            for recipe in truth['recipes']:
                recipe_type = recipe['type'].removeprefix('minecraft:')
                if recipe_type not in EXECUTABLE_TYPES:
                    continue
                payload = dry_run_params(recipe)
                expected = ('smelt' if recipe_type == 'smelting' else 'craft_grid')
                self.assertEqual(payload['method'], expected)
                if expected == 'craft_grid':
                    self.assertEqual(len(payload['params']['layout']), 9)
                else:
                    self.assertEqual(payload['params']['n'], 1)
                counts[(version, recipe_type)] += 1
        for version in ('1.16.5', '1.19', '1.21.11'):
            for recipe_type in EXECUTABLE_TYPES:
                self.assertGreater(counts[(version, recipe_type)], 0)


if __name__ == '__main__':
    unittest.main()
