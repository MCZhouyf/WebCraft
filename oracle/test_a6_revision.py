import json
import unittest
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class A6RevisionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tasks = [json.loads(line) for line in
                     (ROOT / 'data/tasks.jsonl').read_text().splitlines() if line]
        cls.expectations = [json.loads(line) for line in
                            (ROOT / 'data/stale_agent_expectations.jsonl').read_text().splitlines()
                            if line]
        cls.report = json.loads((ROOT / 'reports/a6_revision.json').read_text())

    def test_feasibility_balance_and_lodestone(self):
        archived = [json.loads(line) for line in
                    (ROOT / 'data/archive/amendment_v2_pre_replace_20261005/tasks.jsonl')
                    .read_text().splitlines() if line]
        tasks = [task for task in archived if task['type'] == 'feasibility']
        self.assertGreaterEqual(len(tasks), 10)
        self.assertIn('lodestone', {task['target']['item'] for task in tasks})
        for version in ('1.16.5', '1.21.11'):
            feasible = sum(task['feasible'][version] for task in tasks)
            self.assertGreaterEqual(feasible / len(tasks), 0.30)
            self.assertGreaterEqual((len(tasks) - feasible) / len(tasks), 0.30)

    def test_planning_full_path_cap_and_mechanism_coverage(self):
        self.assertEqual(self.report['planning_reuse_cap'], 6)
        self.assertLessEqual(self.report['planning_maximum_full_path_reuse'], 6)
        self.assertGreaterEqual(self.report['planning_unique_mechanisms'], 8)

    def test_efficiency_metadata_is_complete(self):
        tasks = [task for task in self.tasks if task['type'] == 'efficiency']
        self.assertGreaterEqual(len(tasks), 6)
        for task in tasks:
            evidence = task['efficiency']
            self.assertIn('c_star', evidence)
            self.assertIn('c_stale', evidence)
            self.assertIn('stale_source_world', evidence)
            self.assertTrue(not evidence['stale_replay_feasible'] or
                            evidence['c_stale'] >= 1.5 * evidence['c_star'])

    def test_development_split_and_stale_expectations(self):
        dev = [task for task in self.tasks if task['split'] == 'dev']
        self.assertEqual(len(dev), 8)
        self.assertEqual(Counter(task['type'] for task in dev),
                         Counter({'planning': 5, 'feasibility': 2, 'efficiency': 1}))
        self.assertEqual({task['id'] for task in self.tasks},
                         {row['task_id'] for row in self.expectations})


if __name__ == '__main__':
    unittest.main()
