"""Synthetic structural checks only; no episodes, models or scorer keys."""
from scripts.export_human_audit_v3b import PAIR_FIELDS, assignment_matches, enrich, grid_matches, layouts, snippet_sharing
from scripts.validate_v3b import EVIDENCE, validate_rows


def row(contact='无法确定', propagation=''):
    result = {field: 'evidence' for field in EVIDENCE}
    result.update(audit_id='AX-001', contact_type=contact, propagation=propagation,
                  notes='证据不足，保留无法确定', truncated='0', search_split='0')
    return result


def test_four_value_contact_restores_unknown_without_propagation():
    assert validate_rows([row()], 1)['status'] == 'PASS'
    assert validate_rows([row(propagation='否')], 1)['status'] == 'NEEDS_REVIEW'
    assert validate_rows([row(contact='查询+执行')], 1)['status'] == 'NEEDS_REVIEW'


def test_contact_requires_propagation_but_no_contact_forbids_it():
    assert validate_rows([row('执行', '无法确定')], 1)['status'] == 'PASS'
    assert validate_rows([row('查询')], 1)['status'] == 'NEEDS_REVIEW'
    assert validate_rows([row('无', '否')], 1)['status'] == 'NEEDS_REVIEW'
    assert validate_rows([row('无')], 1)['status'] == 'PASS'


def test_shaped_empty_cells_mirror_and_translation():
    recipe = {'ordered': True, 'grid': [[['a'], []], [[], ['b']]]}
    assert grid_matches(['a', None, None, None, 'b', None, None, None, None], recipe)
    assert grid_matches([None, 'a', None, None, None, 'b', None, None, None], recipe)
    assert grid_matches([None, 'a', None, 'b', None, None, None, None, None], recipe)
    assert not grid_matches(['a', 'b', None, None, None, None, None, None, None], recipe)


def test_shapeless_quantities_and_alternative_matching():
    assert assignment_matches(['a', 'b'], [['a', 'b'], ['a']])
    assert not assignment_matches(['a'], [['a'], ['a']])
    recipe = {'ordered': False, 'ingredients': [['a'], ['a']]}
    assert grid_matches(['a', None, 'a', None, None, None, None, None, None], recipe)
    assert not grid_matches(['a', None, None, None, None, None, None, None, None], recipe)


def test_ingredient_alternatives_have_a_concrete_witness():
    recipe = {'kind': 'recipe', 'station': 'crafting_grid', 'ordered': True,
              'grid': [[['old', 'new']], [['stick']]]}
    assert ['new', None, None, 'stick', None, None, None, None, None] in layouts(recipe)


def test_snippet_sharing_is_exact_and_reports_cross_scope():
    rows = []
    for identifier, method, condition in [('AX-001', 'naive', 'mixed'), ('AX-002', 'arbiter', 'mixed'),
                                           ('AX-003', 'other', 'matched')]:
        rows.append(dict(audit_id=identifier, agent_family='pipeline', method=method,
                         condition=condition, world='1.19', task_id='t', search_split='0', search_snippets='same'))
    sharing, stats = snippet_sharing(rows)
    assert stats['shared_rows'] == 3
    assert sharing['AX-001']['same_task_condition_methods'] == ['arbiter', 'naive']
    assert len(sharing['AX-001']['all_identical_block_rows']) == 3


def test_cds_keeps_known_human_values_and_does_not_force_unknown_to_none():
    class Evidence:
        def cds_pairs(self, row, metadata):
            return [dict(conflict_id='mechanism', **{field: 'mechanism evidence' for field in PAIR_FIELDS})]

    for contact, propagation in [('执行', '无法确定'), ('无法确定', '无法确定')]:
        original = row(contact, propagation)
        sharing, _ = snippet_sharing([original])
        _, output, summary = enrich(list(original), [original], Evidence(), sharing, {'AX-001': {}})
        assert output[0]['contact_type'] == contact
        assert output[0]['prev_propagation_cds_v2'] == propagation
        assert output[0]['propagation'] == (propagation if contact == '执行' else '')
        assert summary[0]['review_required']
