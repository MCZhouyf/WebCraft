from scripts.audit_a11_bia_protocol_20261016 import fact_check, message_facts


def test_initial_knowledge_lost_from_next_stateless_request_is_detected():
    import json

    fact = {'mechanism fact': 'ore drops ingot.', 'source': 'archive', 'timestamp': '2021'}
    first = [{'role': 'user', 'content': json.dumps({
        'observation': {'diagnostic_mechanism_facts': [fact]}, 'trace': [],
    })}]
    later = [{'role': 'user', 'content': json.dumps({
        'observation': {'success': True},
        'trace': [{'observation': {'success': True}}],
    })}]
    assert fact_check([fact], message_facts(first))['passed']
    result = fact_check([fact], message_facts(later))
    assert not result['passed']
    assert result['missing_facts'] == [fact]


def test_structural_fact_check_preserves_source_and_timestamp_and_multiplicity():
    fact = {'mechanism fact': 'ore drops ingot.', 'source': 'archive', 'timestamp': '2021'}
    changed = {**fact, 'timestamp': '2026'}
    assert not fact_check([fact], [changed])['passed']
    assert fact_check([fact, fact], [fact])['missing_entries'] == 1
    assert fact_check([], [])['passed']
