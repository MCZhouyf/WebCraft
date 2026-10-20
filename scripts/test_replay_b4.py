import json
import tempfile
import unittest
from pathlib import Path

from scripts.replay_b4 import compare_online, replay_episode


def event(method, result, primary=0, success=True):
    return {
        'schema_version': 2, 'method': method, 'params': {}, 'result': result,
        'cost': {'primary': primary}, 'item_flow': {'consumed': [], 'produced': []},
        'success': success,
    }


class B4ReplayTest(unittest.TestCase):
    def test_replays_success_and_cost(self):
        task = {
            'target': {'item': 'stick', 'count': 1},
            'feasible': {'1.21.11': True},
            'oracle_plans': {'1.21.11': {'actions': [{}]}},
        }
        events = [
            event('inventory', []),
            event('craft_grid', {'success': True}, 1),
            event('inventory', [{'name': 'stick', 'count': 4}]),
            event('submit', {'confirmed': True}),
        ]
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'episode.jsonl'
            path.write_text(''.join(json.dumps(row) + '\n' for row in events))
            replayed = replay_episode(path, task, '1.21.11')
        self.assertTrue(replayed['success'])
        self.assertEqual(1, replayed['primary_cost'])
        self.assertEqual(['craft_grid'], replayed['action_methods'])
        online = {**replayed}
        self.assertTrue(compare_online(replayed, online)['match'])

    def test_detects_online_difference(self):
        replayed = {'success': True, 'primary_cost': 1}
        online = {'success': False, 'primary_cost': 1}
        comparison = compare_online(replayed, online)
        self.assertFalse(comparison['match'])
        self.assertIn('success', comparison['differences'])


if __name__ == '__main__':
    unittest.main()
