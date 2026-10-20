import json
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class AmendmentV2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = [json.loads(line) for line in
                     (ROOT / 'data/tasks.jsonl').read_text().splitlines() if line]
        cls.expectations = [json.loads(line) for line in
                            (ROOT / 'data/stale_agent_expectations.jsonl').read_text().splitlines()
                            if line]
        cls.arena = json.loads((ROOT / 'harness/config/arena.json').read_text())
        cls.amendment = (ROOT / 'prereg/amendment_v2.md').read_text()
        cls.agent_source = (ROOT / 'harness/src/semantic_agent.mjs').read_text()

    def test_original_task_is_not_replaced(self):
        task = next(row for row in self.tasks if row['id'] == 'fea_009')
        self.assertEqual('iron_ore', task['target']['item'])
        self.assertEqual('dev', task['split'])
        self.assertEqual('feasibility', task['type'])

    def test_task_and_expectation_ids_remain_aligned(self):
        self.assertEqual({row['id'] for row in self.tasks},
                         {row['task_id'] for row in self.expectations})
        self.assertEqual(42, len(self.tasks))

    def test_teleport_policy_is_frozen(self):
        targets = [self.arena['workbench'], self.arena['furnace'], *self.arena['resources']]
        self.assertTrue(all('station' in row for row in targets))
        self.assertTrue(all('pickup' in row for row in self.arena['resources']))
        self.assertNotIn('.goto(', self.agent_source)
        self.assertIn("net_displacement: 'not_applicable'", self.agent_source)
        self.assertIn('automatically retried exactly once', self.amendment)
        self.assertIn('data get entity', self.agent_source)
        self.assertNotIn('data/truth', self.agent_source)


if __name__ == '__main__':
    unittest.main()
