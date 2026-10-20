from scripts.reexport_semantic_audit_v2 import full_episode, sequence, split_cell


def test_complete_episode_preserves_long_feedback_and_stops_at_submit():
    events = [{'method': 'inventory', 'params': {}, 'result': []},
              {'method': 'craft_grid', 'params': {'layout': ['x'] * 9},
               'result': {'explanation': 'x' * 1500}, 'intent': 'probe'},
              {'method': 'submit', 'params': {'result': {'feasible': False}}, 'result': {}},
              {'method': 'inventory', 'params': {}, 'result': []}]
    exported = full_episode(events)
    assert len(exported) == 3
    text = sequence(exported)
    assert 'x' * 1500 in text
    assert 'intent' not in text
    assert text.count('"method":"inventory"') == 1
    assert '"method":"submit"' in text


def test_cell_splitting_is_lossless_and_respects_utf16_for_supplementary_characters():
    text = 'a' * 9 + '\U0001f600' * 5 + 'z' * 11
    parts = split_cell(text, limit=10)
    assert ''.join(parts) == text
    assert all(len(part.encode('utf-16-le')) // 2 <= 10 for part in parts)
