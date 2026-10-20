"""Synthetic export checks; no policy changes or held-out execution."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from analyze_a22_pf_rag import paired_ci, divergence, stale_adoption


def test_identical_paired_vectors_have_zero_interval():
    result = paired_ci([('a', 0), ('a', 0), ('b', 0)])
    assert result['estimate'] == result['ci_low'] == result['ci_high'] == 0
    assert result['clusters'] == 2


def test_cluster_bootstrap_is_reproducible_and_counts_tasks_not_replicas():
    values = [('a', 1), ('a', 0), ('b', -1), ('c', 1)]
    assert paired_ci(values) == paired_ci(values)
    assert paired_ci(values)['clusters'] == 3
    assert paired_ci(values)['estimate'] == .25


def test_missing_and_single_cluster_intervals_are_not_invented():
    assert paired_ci([])['estimate'] is None
    assert paired_ci([('a', 1)])['ci_low'] is None


def test_action_identity_ignores_intent_but_not_parameters():
    action = {'method': 'check_name', 'params': {'identifier': 'item'},
              'result': {'resolvable': True}, 'cost': {'primary': 1}, 'intent': 'probe'}
    left = {'events': [action]}
    assert divergence(left, {'events': [{**action, 'intent': 'commit'}]}) == (None, 'identical')
    assert divergence(left, {'events': [{**action, 'params': {'identifier': 'other'}}]}) == (0, 'parameters')


def test_executed_step_is_not_substituted_for_missing_full_plan():
    record = {'method': 'naive_rag', 'world': 'sandbox',
              'policy_trace': [{'mechanism_id': 'm'}]}
    assert stale_adoption(record, {}) is None
