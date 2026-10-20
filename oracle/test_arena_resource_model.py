import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class ArenaResourceModelTests(unittest.TestCase):
    def setUp(self):
        self.arena = json.loads((ROOT / 'harness/config/arena.json').read_text())
        self.model = json.loads((ROOT / 'config/resource_model.json').read_text())

    def test_resource_counts_and_ids(self):
        resources = self.arena['resources']
        self.assertEqual(Counter(row['block'] for row in resources),
                         Counter(self.model['resource_block_counts']))
        self.assertEqual(len({row['nodeId'] for row in resources}), len(resources))

    def test_positions_are_unique_and_inside_bounds(self):
        resources = self.arena['resources']
        self.assertEqual(len({(row['x'], row['y'], row['z']) for row in resources}),
                         len(resources))
        bounds = self.arena['bounds']
        for row in resources:
            for axis in ('x', 'y', 'z'):
                self.assertGreaterEqual(row[axis], bounds['min'][axis])
                self.assertLessEqual(row[axis], bounds['max'][axis])

        occupied = {(row['x'], row['y'], row['z']) for row in resources}
        occupied.update({
            (self.arena['workbench']['x'], self.arena['workbench']['y'],
             self.arena['workbench']['z']),
            (self.arena['furnace']['x'], self.arena['furnace']['y'],
             self.arena['furnace']['z']),
        })
        for target in [self.arena['workbench'], self.arena['furnace'], *resources]:
            station = target['station']
            station_block = (int(station['x'] - 0.5), station['y'],
                             int(station['z'] - 0.5))
            self.assertNotIn(station_block, occupied)
            self.assertEqual(1, abs(station_block[0] - target['x'])
                             + abs(station_block[2] - target['z']))
            self.assertEqual(target['y'], station['y'])
        for resource in resources:
            self.assertEqual(
                {'x': resource['x'] + 0.5, 'y': resource['y'],
                 'z': resource['z'] + 0.5},
                resource['pickup'],
            )

    def test_standard_inventory_matches_resource_model(self):
        self.assertEqual(self.arena['initialItems'], self.model['standard_inventory'])


if __name__ == '__main__':
    unittest.main()
