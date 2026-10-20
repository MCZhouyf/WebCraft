import tempfile
import unittest
from pathlib import Path

from sim.parity import _equivalent, replay_trace


class ParityTests(unittest.TestCase):
    def test_equivalence_ignores_live_only_wall_time(self):
        live = {'success': True, 'cost': {'primary': 1, 'wall_ms': 300},
                'item_flow': {'consumed': [], 'produced': [{'name': 'oak_log', 'count': 1}]},
                'result': {'success': True, 'items': [{'name': 'oak_log', 'count': 1}]}}
        simulated = {'success': True, 'cost': {'primary': 1, 'wall_ms': 0},
                     'item_flow': live['item_flow'], 'result': live['result']}
        self.assertEqual(_equivalent(live, simulated), [])

    def test_d1_accepts_only_minimum_random_drop_quantity(self):
        live = {'success': True, 'cost': {'primary': 1},
                'item_flow': {'consumed': [], 'produced': [{'name': 'lapis_lazuli', 'count': 5}]},
                'result': {'success': True, 'items': [{'name': 'lapis_lazuli', 'count': 5}]}}
        simulated = {'success': True, 'cost': {'primary': 1},
                     'item_flow': {'consumed': [], 'produced': [{'name': 'lapis_lazuli', 'count': 4}]},
                     'result': {'success': True, 'items': [{'name': 'lapis_lazuli', 'count': 4}]}}
        self.assertEqual(_equivalent(live, simulated), [])

    def test_replay_trace_uses_logged_initial_inventory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            live = root / 'live.jsonl'
            live.write_text(
                '{"schema_version":2,"method":"inventory","params":{},"result":[{"name":"stone_pickaxe","count":1}],"cost":{"primary":0},"item_flow":{"consumed":[],"produced":[]},"success":true}\n'
                '{"schema_version":2,"method":"mine","params":{"nodeId":"iron_1"},"result":{"success":true,"items":[{"name":"raw_iron","count":1}]},"cost":{"primary":1},"item_flow":{"consumed":[],"produced":[{"name":"raw_iron","count":1}]},"success":true}\n'
            )
            outcome = replay_trace({'path': live, 'world': '1.19', 'task_id': 'unit',
                                    'attempt': 1, 'role': 'oracle'}, root / 'output')
        self.assertTrue(outcome['match'], outcome)
