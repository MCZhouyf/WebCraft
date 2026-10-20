import unittest

from sim.runner import load_tasks, run_oracle


class RunnerTests(unittest.TestCase):
    def test_current_oracle_plan_reaches_frozen_goal(self):
        task = load_tasks()['eff_007']
        result = run_oracle(task, '1.21.11')
        self.assertTrue(result['success'], result)
        self.assertEqual(result['primary_cost'], result['oracle_cost_current_world'])
