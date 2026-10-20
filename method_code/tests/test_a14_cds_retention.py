"""Synthetic offline checks; never execute an agent or open a scorer key."""
import json
from pathlib import Path

from scripts.analyze_a14_cds_retention import (
    frozen_parser, interval, prefix_state, reconstruct, request_pairs, retention,
)

FACT = {'mechanism fact': 'gem can be made from rock at crafting_grid.',
        'source': 'source_a', 'timestamp': '2000-01-01T00:00:00Z'}
ASSERTION = {'kind': 'recipe', 'output': 'gem', 'ingredients': [['rock']],
             'ordered': False, 'station': 'crafting_grid', 'count': 1}


def mechanisms():
    import hashlib
    return {hashlib.sha256(FACT['mechanism fact'].encode()).hexdigest(): ASSERTION}


def test_full_requires_text_source_and_time_and_preserves_multiplicity():
    rows, counts = retention([FACT, FACT], {'observation': [FACT]}, mechanisms())
    assert counts['full_entries'] == 1
    assert counts['full_unique_triples'] == 1
    assert counts['expected_unique_triples'] == 1
    assert counts['all_full'] == 0
    assert rows[1]['retention_class'] == 'semantic_trace_only'


def test_fact_text_without_provenance_is_only_trace():
    rows, counts = retention([FACT], {'observation': {'text': FACT['mechanism fact']}}, mechanisms())
    assert counts['full_entries'] == 0
    assert rows[0]['retention_class'] == 'semantic_trace_only'


def test_task_and_inventory_alone_do_not_establish_semantic_mechanism():
    rows, _ = retention([FACT], {'task': {'goal': 'gem', 'input': 'rock'},
                                'observation': [{'name': 'gem'}, {'name': 'rock'}]}, mechanisms())
    assert rows[0]['retention_class'] == 'no_trace'


def test_zero_actual_facts_not_condition_name_gets_na():
    _, counts = retention([], {'observation': []}, {})
    assert counts['vacuous'] == 1 and counts['full_fraction'] == ''
    _, counts = retention([FACT], {'observation': []}, mechanisms())
    assert counts['vacuous'] == 0 and counts['full_fraction'] == 0


def test_prefix_success_requires_affirmative_submit_in_prefix():
    events = [
        {'method': 'inventory', 'result': [], 'params': {}},
        {'method': 'craft_grid', 'result': {'success': True}, 'params': {},
         'item_flow': {'produced': [{'name': 'gem', 'count': 1}]}},
        {'method': 'submit', 'result': {}, 'params': {'result': {'feasible': True}}},
    ]
    first = prefix_state(events, {1: 1, 2: 2}, 1, {'item': 'gem', 'count': 1}, 2)
    assert first['prefix_success'] == 0 and first['prefix_goal_attained'] == 1
    assert first['prefix_censored'] == 1
    second = prefix_state(events, {1: 1, 2: 2}, 2, {'item': 'gem', 'count': 1}, 2)
    assert second['prefix_success'] == 1 and second['prefix_censored'] == 0
    assert prefix_state(events, {}, 2, {'item': 'gem', 'count': 1}, 2)['prefix_success'] == 0


def test_sparse_intervals_are_blank_not_degenerate_precision():
    result = interval([0] * 52, list(range(52)), contrast=True, unit='directed_instance', contact_positives=0)
    assert result['estimate'] == 0 and result['ci95_low'] == ''
    assert 'fewer_than_5_contact' in result['ci_status']
    result = interval([1] * 2, ['a', 'b'], contrast=False)
    assert result['ci95_low'] == '' and result['n'] == 2


def test_nested_canary_does_not_hide_outer_action_response():
    log = [
        {'record_type': 'full_prompt', 'call_type': 'external_react'},
        {'record_type': 'full_prompt', 'call_type': 'canary'},
        {'record_type': 'full_response', 'call_type': 'canary', 'content': 'not_action_response'},
        {'record_type': 'full_response', 'call_type': 'external_react', 'content': 'action_response'},
    ]
    pairs = request_pairs(log, 'react')
    assert len(pairs) == 1 and pairs[0][2]['content'] == 'action_response'


def test_independent_request_reconstruction_and_event_mapping():
    root = Path(__file__).resolve().parents[1]
    helpers = frozen_parser((root / 'src/arbiter/evaluation/external_agents.py').read_text())
    task = {'target': {'item': 'gem', 'count': 1}, 'standard_items': []}
    initial = {'inventory': [], 'diagnostic_mechanism_facts': [FACT]}
    action = {'action': 'submit', 'args': {'feasible': False}, 'intent': 'commit'}
    payload = {'task': {'goal': {'item': 'gem', 'count': 1}, 'initial_inventory': []},
               'observation': initial, 'trace': []}
    messages = [{'role': 'system', 'content': helpers['public_system_prompt']('direct_llm')},
                {'role': 'user', 'content': json.dumps(payload, sort_keys=True)}]
    request = {'messages': messages}
    response = {'content': json.dumps(action), 'finish_reason': 'stop'}
    boundary = {'episode_trace': [
        {'action': 'inventory', 'args': {}, 'observation': initial},
        {'action': 'submit', 'args': {'result': {'feasible': False}}, 'observation': {'success': False}},
    ]}
    events = [{'method': 'inventory', 'result': []}, {'method': 'submit', 'result': {'success': False}}]
    mapping, checks = reconstruct([(0, request, response)], boundary, events, task, 'direct_llm', helpers)
    assert mapping == {1: 1}
    assert checks[0]['reconstruction_equal'] == 1
