"""Synthetic calibration cases only; never read a human audit key."""
import pytest

from scripts.analyze_a19_human_audit import (
    aggregate_contact, aggregate_propagation, binary_counts, confusion,
    human_propagation, immutable_check, indexed,
)


def test_contact_precedence_across_all_event_aliases():
    assert aggregate_contact(['无', '查询', '执行']) == '执行'
    assert aggregate_contact(['无', '查询']) == '查询'
    assert aggregate_contact(['无', '无']) == '无'
    assert aggregate_contact(['无', '无法确定']) == '无法确定'


def test_any_propagation_positive_wins_without_unknown_as_negative():
    assert aggregate_propagation(['否', '是', '无法确定']) == '是'
    assert aggregate_propagation(['否', '无法确定']) == '无法确定'
    assert aggregate_propagation(['否', '否']) == '否'


def test_human_structural_blanks_are_not_all_negative():
    assert human_propagation({'contact_type': '无', 'propagation': ''}) == '否'
    assert human_propagation({'contact_type': '无法确定', 'propagation': ''}) == '无法确定'
    assert human_propagation({'contact_type': '执行', 'propagation': '无法确定'}) == '无法确定'


def test_binary_contact_excludes_unresolved_and_keeps_subtypes_positive():
    rows = [{'human_contact': h, 'scorer_contact': s} for h, s in
            [('查询', '执行'), ('无', '查询'), ('执行', '无'),
             ('无法确定', '执行'), ('无', '无')]]
    result = binary_counts(rows, 'contact')
    assert [result[k] for k in ('TP', 'FP', 'FN', 'TN')] == [1, 1, 1, 1]
    assert result['precision'] == result['recall'] == 0.5
    assert result['n_eligible'] == 4
    assert sum(r['row_total'] for r in confusion(rows, 'contact')) == 5
    assert len(confusion(rows, 'contact')) == 4


def test_propagation_zero_denominator_is_not_perfect():
    result = binary_counts([{'human_propagation': '否', 'scorer_propagation': '否'}], 'propagation')
    assert result['precision'] == result['recall'] == ''


def test_duplicate_ids_are_not_silently_overwritten():
    with pytest.raises(RuntimeError, match='duplicate'):
        indexed([{'audit_id': 'AX-001'}, {'audit_id': 'AX-001'}])


def test_immutable_evidence_changes_rejected_annotations_allowed():
    original = [{'audit_id': 'AX-001', 'evidence': 'full episode', 'contact_type': ''}]
    immutable_check([{'audit_id': 'AX-001', 'evidence': 'full episode', 'contact_type': '查询'}], original)
    with pytest.raises(RuntimeError, match='evidence'):
        immutable_check([{'audit_id': 'AX-001', 'evidence': 'truncated', 'contact_type': '查询'}], original)


def test_unknown_schema_labels_fail_instead_of_mapping_to_no_contact():
    with pytest.raises(ValueError):
        aggregate_contact(['unexpected'])
    with pytest.raises(ValueError):
        aggregate_propagation([])
