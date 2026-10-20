from scripts.validate_v3 import validate_rows


def row(contact='无', propagation='', **kwargs):
    return {'audit_id': 'AX-001', 'contact_type': contact, 'propagation': propagation,
            'notes': '第2步无相关接触。', 'search_snippets': '该回合无检索',
            'action_sequence_and_feedback': '完整动作', 'agent_family': 'external',
            'method': 'react', 'condition': 'mixed', 'world': 'example', 'task_id': 'example',
            'n_actions': '3', 'truncated': '0', **kwargs}


def test_no_contact_requires_blank_propagation():
    assert validate_rows([row()], 1)['status'] == 'PASS'
    assert validate_rows([row(propagation='否')], 1)['status'] == 'NEEDS_REVIEW'


def test_contact_requires_three_valued_propagation():
    assert validate_rows([row('查询', '无法确定')], 1)['status'] == 'PASS'
    assert validate_rows([row('执行', '是')], 1)['status'] == 'PASS'
    assert validate_rows([row('执行', '')], 1)['status'] == 'NEEDS_REVIEW'


def test_old_contact_categories_are_rejected_and_prev_values_are_not_validated():
    assert validate_rows([row('无法确定')], 1)['status'] == 'NEEDS_REVIEW'
    assert validate_rows([row('查询+执行', '是')], 1)['status'] == 'NEEDS_REVIEW'
    assert validate_rows([row('执行', '否', prev_contact_type='查询+执行')], 1)['status'] == 'PASS'


def test_notes_and_unique_ids_are_required():
    assert validate_rows([row(notes='')], 1)['status'] == 'NEEDS_REVIEW'
    assert validate_rows([row(), row()], 2)['status'] == 'NEEDS_REVIEW'
