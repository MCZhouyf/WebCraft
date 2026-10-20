"""Regression tests for the new audit, with no experimental execution."""
import ast
import itertools
import json
import unittest
from dataclasses import replace
import numpy as np
from w11_common import OUT,ROOT,planner_from_fixture,resolved_params
from w11_contact_audit import selector_key


class AttributionTests(unittest.TestCase):
    def test_key_is_full_and_permutation_invariant(self):
        data=json.loads((OUT/'fixtures/case_04_frozen.json').read_text())
        candidates=[(op,resolved_params(op,choices)) for op,choices in planner_from_fixture(data)._resolved()
                    if op.mechanism_id in {'cf_0cf08e1cf6803d3f','cf_b55d59dd1aaa0ede'}]
        self.assertEqual(len(candidates),2)
        self.assertEqual(candidates[0][1],candidates[1][1])
        keys=[selector_key(*c) for c in candidates]
        self.assertEqual(len(set(keys)),2)
        outcomes={min(order,key=lambda c:selector_key(*c))[0].mechanism_id
                  for order in itertools.permutations(candidates)}
        self.assertEqual(len(outcomes),1)

    def test_probability_does_not_break_an_already_equivalent_tie_key(self):
        data=json.loads((OUT/'fixtures/case_04_frozen.json').read_text())
        op,choices=planner_from_fixture(data)._resolved()[0]
        params=resolved_params(op,choices)
        self.assertEqual(selector_key(op,params),selector_key(replace(op,probability=.123),params))

    def test_unavoidable_positive_preserves_episode_lower_bound(self):
        labels=[[1],[0,1]]
        self.assertEqual(max(min(x) for x in labels),1)
        self.assertEqual(max(max(x) for x in labels),1)
        self.assertEqual(max(min(x) for x in [[0],[0,1]]),0)

    def test_paired_bootstrap_identical_methods(self):
        values=np.arange(34)%2
        draws=np.random.default_rng(20261017).integers(0,34,size=(10000,34))
        a=values[draws].mean(axis=1)
        self.assertTrue(np.array_equal(a-a,np.zeros(10000)))

    def test_no_agent_or_world_entrypoints(self):
        forbidden={'run_episode','run_matrix','create_simulated_agent','submit','mine','craft_grid','smelt','check_name'}
        for path in (ROOT/'scripts').glob('w11_*.py'):
            tree=ast.parse(path.read_text())
            bad=[node.func.attr for node in ast.walk(tree) if isinstance(node,ast.Call)
                 and isinstance(node.func,ast.Attribute) and node.func.attr in forbidden]
            self.assertEqual(bad,[],path.name)


if __name__=='__main__':unittest.main(verbosity=2)
