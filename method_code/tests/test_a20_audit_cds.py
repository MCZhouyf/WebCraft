"""Synthetic offline A20 checks. No key files, environment episodes, or LLMs."""
import math

from scripts.analyze_a20_audit_cds import (
    corrected_bounds, downstream_proof, mechanism_category, normalize_contact,
    primary_category, rationale_category, wilson,
)


def test_wilson_limits_and_zero_denominator():
    assert wilson(0, 0) == ('', '')
    low, high = wilson(0, 10)
    assert math.isclose(low, 0, abs_tol=1e-12)
    assert 0.27 < high < 0.28
    low, high = wilson(10, 10)
    assert 0.72 < low < 0.73
    assert math.isclose(high, 1)


def test_cds_action_query_encoding_uses_a19_precedence():
    assert normalize_contact('action+query') == '执行'
    assert normalize_contact('query') == '查询'
    assert normalize_contact('none') == '无'


def test_target_valid_recipe_is_not_stale_specific_when_catalog_is_incomplete():
    result = mechanism_category(
        assertion={'output': 'blue_wool', 'count': 1},
        target_outcome={'product': 'blue_wool', 'count': 1},
        outcomes={}, method='craft_grid', dependency=[], target_truth=False,
        comparison_truth=True, world_consistent=True)
    assert result[0] == 'ii'


def test_reference_world_defect_is_explicit():
    result = mechanism_category(
        assertion={}, target_outcome={}, outcomes={}, method='mine', dependency=[],
        target_truth=True, comparison_truth=False, world_consistent=True)
    assert result[0] == 'iii'


def test_stale_only_name_is_specific_not_just_interface_legality():
    result = mechanism_category(
        assertion={'exists': True}, target_outcome={'resolvable': False},
        outcomes={'old': {'resolvable': True}, 'current': {'resolvable': False}},
        method='check_name', dependency=[], target_truth=False,
        comparison_truth=True, world_consistent=True)
    assert result[0] == 'i'


def test_absent_material_input_but_not_name_query_is_dependency():
    events = [
        {'method': 'inventory', 'result': []},
        {'method': 'mine', 'result': {}, 'item_flow': {}},
        {'method': 'check_name', 'params': {'name': 'raw_iron'}},
        {'method': 'smelt', 'params': {'input': 'raw_iron'}, 'item_flow': {}},
    ]
    evidence = downstream_proof(events, 1, {'raw_iron'})
    assert [r['event_index'] for r in evidence] == [3]


def test_initial_same_name_item_is_not_a_stale_batch():
    events = [
        {'method': 'inventory', 'result': {'inventory': [{'name': 'iron_ore', 'count': 1}]}},
        {'method': 'mine', 'result': {}, 'item_flow': {}},
        {'method': 'smelt', 'params': {'input': 'iron_ore'},
         'item_flow': {'consumed': [{'name': 'iron_ore', 'count': 1}]}},
    ]
    assert downstream_proof(events, 1, {'iron_ore'}) == []


def test_shared_mining_without_dependency_is_non_specific():
    result = mechanism_category(
        assertion={'drops': ['iron_ore']}, target_outcome={'product': ['raw_iron', 1]},
        outcomes={'old': {'product': ['iron_ore', 1]}}, method='mine', dependency=[],
        target_truth=False, comparison_truth=True, world_consistent=True)
    assert result[0] == 'ii'


def test_uncertainty_is_preserved_in_episode_bounds():
    assert corrected_bounds(['ii', 'iv']) == (0, 1)
    assert corrected_bounds(['i', 'iv']) == (1, 1)
    assert corrected_bounds(['ii', 'iii']) == (0, 0)
    assert primary_category(['ii', 'iv', 'i']) == 'i'


def test_notes_without_explicit_reason_are_other():
    assert rationale_category('没有足够信息判断。')[0] == 'other'
    assert rationale_category('两个版本均可使用相同配方。')[0] == 'shared_or_current_valid'


def test_same_materials_repeated_does_not_mean_old_and_new_both_valid():
    assert rationale_category('被否定后仍试相同材料的布局，构成继续依赖。')[0] == 'other'
