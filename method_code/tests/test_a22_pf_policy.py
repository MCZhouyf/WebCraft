"""Synthetic public-evidence fixtures only; no simulator or benchmark inputs."""
from collections import Counter
from dataclasses import replace

import pytest

from arbiter.agent.aggregation import SearchDerivedClaim, naive_rag
from arbiter.agent.core import ArbiterAgent, NAIVE_EXECUTION, ExecutionPolicy
from arbiter.agent.pf_rag import FilterState, PFRAGAgent, identifiers, is_refutation
from arbiter.agent.planning import MechanismOperator, PlanStep, CandidatePlan
from arbiter.contracts import Goal


def op(key, ingredient='a', output='goal'):
    return MechanismOperator(key, 'craft_grid', {}, ((ingredient,),), output,
                             layout_slots=(0,))


def claim(key, revision='r', site='public', timestamp='2020-01-01'):
    return SearchDerivedClaim({
        'text': 'A synthetic recipe.', 'source_site': site, 'page_title': 'Recipe',
        'revid': revision, 'timestamp': timestamp, 'chunk_id': key,
    }, key, True)


def state(ops, revisions=None):
    return FilterState(ops, [claim(o.mechanism_id, (revisions or {}).get(o.mechanism_id, 'r'))
                            for o in ops])


def test_symmetric_difference_scores_and_stable_tie():
    s = state([op('a', 'oak'), op('b', 'birch'), op('c', 'spruce')])
    assert s.scores() == {'oak': 2, 'birch': 2, 'spruce': 2}
    assert s.next_probe() == ('birch', 2)


def test_duplicate_payload_is_not_extra_conflict():
    s = state([op('a'), op('b'), op('c', 'b')])
    assert len(s.conflict_pairs()) == 1
    assert s.scores() == {'a': 1, 'b': 1}


def test_absence_filters_across_relations_and_support_does_not_confirm():
    s = state([op('a'), op('b', 'b'), op('c', 'a', 'other')])
    s.observe_name('a', {'resolvable': False})
    assert s.removed == {'a', 'c'}
    s.observe_name('b', {'resolvable': True})
    assert s.removed == {'a', 'c'}
    assert not s.suspect_revisions()


def test_unknown_not_false_and_no_duplicate_probe():
    s = state([op('a'), op('b', 'b')])
    s.observe_name('a', {})
    assert not s.removed
    assert s.next_probe() == ('b', 1)


def test_suspect_does_not_delete_other_candidate_and_sites_do_not_collide():
    s = FilterState([op('a'), op('b', 'b')],
                    [claim('a', site='one'), claim('b', site='two')])
    s.retire('a', 'execution_refutation')
    assert s.suspect_revisions() == {('one', 'Recipe', 'r')}
    assert [c.operator.mechanism_id for c in s.active()] == ['b']


def test_positive_exists_blocks_suspect_even_after_execution_denial():
    s = state([op('a'), op('b', 'b')])
    s.observe_name('b', {'resolvable': True})
    s.retire('a', 'execution_refutation')
    assert not s.suspect_revisions()
    assert s.removed == {'a'}


def test_fallback_only_uses_outputs_of_conflicts():
    s = state([op('a'), op('b', 'b')])
    assert s.next_probe(fallback=True) == ('goal', 1)
    assert state([op('a')]).next_probe(fallback=True) is None


def test_identifiers_are_only_explicit_fields():
    assert identifiers(op('a')) == {'a', 'goal'}
    assert identifiers(replace(op('a'), params={'station': 'crafting_table'})) == {
        'a', 'goal', 'crafting_table'}


def plan(key, objective=1):
    step = PlanStep(key, 'craft_grid', {'layout': ['a'] + [None] * 8}, 'goal', 1, 1)
    return CandidatePlan((step,), (key,), 1, objective, (1,))


def test_revision_priority_changes_only_equal_objective_plan_tie():
    s = state([op('a'), op('b', 'b'), op('rejected', 'x', 'other')],
              {'a': 'bad', 'b': 'good', 'rejected': 'bad'})
    s.retire('rejected', 'execution_refutation')
    assert s.choose_plan([plan('a'), plan('b')]).mechanism_ids == ('b',)
    assert s.choose_plan([plan('a'), plan('b', 2)]).mechanism_ids == ('a',)
    assert {c.operator.mechanism_id for c in s.active()} == {'a', 'b'}


def test_without_suspect_original_plan_order_preserved():
    s = state([op('a'), op('b', 'b')])
    assert s.choose_plan([plan('b'), plan('a')]).mechanism_ids == ('b',)


@pytest.mark.parametrize('result', [
    {'success': False, 'error': 'missing ingredient'},
    {'success': False, 'error': 'action could not be completed'},
    {},
])
def test_ambiguous_or_resource_failure_is_not_refutation(result):
    assert not is_refutation(op('a'), plan('a').steps[0], Counter(a=1), result)


def test_wrong_output_with_satisfied_inputs_is_refutation():
    response = {'success': True, 'product': {'name': 'wrong', 'count': 1}}
    assert is_refutation(op('a'), plan('a').steps[0], Counter(a=1), response)
    assert not is_refutation(op('a'), plan('a').steps[0], Counter(), response)


def test_public_recipe_rejection_requires_visible_inputs():
    response = {'success': False, 'product': None, 'cost': {'primary': 1}}
    assert is_refutation(op('a'), plan('a').steps[0], Counter(a=1), response)
    assert not is_refutation(op('a'), plan('a').steps[0], Counter(), response)


class FakeAPI:
    def __init__(self, response=None):
        self.items = Counter(a=1)
        self.calls = []
        self.response = response or {'success': True, 'product': {'name': 'goal', 'count': 1}}

    def inventory(self):
        return [{'name': k, 'count': v} for k, v in self.items.items()]

    def craft_grid(self, layout, *, intent):
        self.calls.append(('craft_grid', layout, intent))
        if self.response.get('success'):
            self.items['a'] -= 1
            self.items[self.response['product']['name']] += self.response['product']['count']
        return dict(self.response)

    def check_name(self, identifier, *, intent):
        self.calls.append(('check_name', identifier, intent))
        return {'resolvable': True}

    def submit(self, result):
        self.calls.append(('submit', result))


@pytest.mark.parametrize('response', [
    {'success': True, 'product': {'name': 'goal', 'count': 1}},
    {'success': False, 'error': 'missing ingredient'},
    {'success': True, 'product': {'name': 'wrong', 'count': 1}},
])
def test_no_conflict_same_semantic_trace_as_naive(response):
    ops, claims = [op('a')], [claim('a')]
    naive_api, pf_api = FakeAPI(response), FakeAPI(response)
    naive = ArbiterAgent(Goal('goal', 1), [], naive_api)
    naive.configure(ops, naive_rag(claims).belief)
    pf = PFRAGAgent(Goal('goal', 1), [], pf_api)
    pf.configure_public(ops, claims, budget=3)
    a = naive.solve(strategy=NAIVE_EXECUTION)
    b = pf.solve()
    assert naive_api.calls == pf_api.calls
    assert a['feasible'] == b['feasible']
    assert b['probe_count'] == 0


@pytest.mark.parametrize('budget', [1, 3, 5])
def test_probe_count_obeys_B_and_total_action_limit(budget):
    ops = [op(str(i), chr(97 + i)) for i in range(7)]
    api = FakeAPI()
    agent = PFRAGAgent(Goal('goal', 1), [], api)
    agent.configure_public(ops, [claim(o.mechanism_id) for o in ops], budget=budget)
    result = agent.solve(policy=ExecutionPolicy(max_actions=1))
    assert result['probe_count'] == 1
    assert len([r for r in api.calls if r[0] != 'submit']) == 1


def test_bad_B_and_provenance_are_rejected():
    agent = PFRAGAgent(Goal('goal', 1), [], FakeAPI())
    with pytest.raises(ValueError):
        agent.configure_public([op('a')], [claim('a')], budget=2)
    with pytest.raises(ValueError):
        FilterState([op('a')], [])
