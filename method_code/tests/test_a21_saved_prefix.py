import json

import pytest

from scripts.a21_saved_prefix import matches, read_prefix


def test_saved_prefix_skips_nested_canaries_and_retains_unanswered_prompt(tmp_path):
    prompt = {'record_type': 'full_prompt', 'call_type': 'external_react',
              'messages': [{'role': 'user', 'content': 'public observation'}],
              'parameters': {'temperature': 0, 'max_tokens': 4096, 'json_object': True, 'thinking': 'disabled'}}
    response = {'record_type': 'full_response', 'call_type': 'external_react', 'model': 'glm-5.2'}
    rows = [prompt, {'record_type': 'full_prompt', 'call_type': 'canary'},
            {'record_type': 'full_response', 'call_type': 'canary'}, response, prompt,
            {'record_type': 'response_error', 'call_type': 'external_react'}]
    path = tmp_path / 'prompts.jsonl'
    path.write_text(''.join(json.dumps(row)+'\n' for row in rows))
    pairs, unanswered = read_prefix(path)
    assert pairs == [(prompt, response)]
    assert unanswered == prompt
    assert matches(prompt, 'external_react', prompt['messages'], prompt['parameters'])
    assert not matches(prompt, 'external_react', [{'role': 'user', 'content': 'changed'}], prompt['parameters'])


def test_saved_prefix_rejects_model_identity_change(tmp_path):
    path = tmp_path / 'prompts.jsonl'
    path.write_text(json.dumps({'record_type': 'full_prompt', 'call_type': 'external_react'})+'\n'+
                    json.dumps({'record_type': 'full_response', 'call_type': 'external_react', 'model': 'other'})+'\n')
    with pytest.raises(ValueError, match='identity'):
        read_prefix(path)
