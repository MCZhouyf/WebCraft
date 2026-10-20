"""Saved-response state restoration must never alter a model request."""
import hashlib
import json
from types import SimpleNamespace

import pytest

from scripts.a18_remote_backend import Coordinator, RemoteError
from scripts.a18_saved_prefix import SavedPrefixClient, SavedPrefixEnd

KWARGS = {'temperature': 0.0, 'max_tokens': 4096, 'json_object': True, 'thinking': 'disabled'}
MESSAGES = [{'role': 'user', 'content': 'synthetic-test-only'}]


def fixture(tmp_path):
    prompt = {'record_type': 'full_prompt', 'call_type': 'external_react', 'messages': MESSAGES,
              'parameters': KWARGS, 'request_sha256': hashlib.sha256(json.dumps(
                  MESSAGES, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}
    response = {'record_type': 'full_response', 'call_type': 'external_react', 'model': 'synthetic-test-model',
                'content': '{"synthetic":true}', 'finish_reason': 'stop', 'cost': None,
                'usage': {'prompt_tokens': 11, 'completion_tokens': 13}}
    source = tmp_path / 'source.jsonl'
    source.write_text(json.dumps(prompt) + '\n' + json.dumps(response) + '\n')
    coordinator = Coordinator(SimpleNamespace(model='synthetic-test-model'), tmp_path)
    client = SavedPrefixClient(coordinator, source, offline_only=True)
    client.audit_path = tmp_path / 'replayed.jsonl'
    return client, coordinator, source


def test_offline_replay_counts_original_budget_not_new_generations(tmp_path):
    client, coordinator, _ = fixture(tmp_path)
    assert client.chat('external_react', MESSAGES, **KWARGS)['content'] == '{"synthetic":true}'
    assert client.replayed == client.task_calls == 1
    assert client.task_input_tokens == 11 and client.task_output_tokens == 13
    assert coordinator.calls == coordinator.task_calls == 0
    assert not (tmp_path / 'ledger.jsonl').exists()
    with pytest.raises(SavedPrefixEnd):
        client.chat('external_react', MESSAGES, **KWARGS)


def test_changed_prompt_fails_before_any_network_request(tmp_path):
    client, coordinator, _ = fixture(tmp_path)
    with pytest.raises(RemoteError, match='diverged'):
        client.chat('external_react', [{'role': 'user', 'content': 'different'}], **KWARGS)
    assert client.replayed == coordinator.calls == 0


def test_changed_model_identity_in_saved_reply_is_rejected(tmp_path):
    _, coordinator, source = fixture(tmp_path)
    text = source.read_text().replace('synthetic-test-model', 'different-test-model')
    source.write_text(text)
    with pytest.raises(RemoteError, match='identity'):
        SavedPrefixClient(coordinator, source, offline_only=True)


def test_all_four_real_interrupted_prefixes_reconstruct_without_calls():
    from scripts.resume_a18_remote_cds import context, missing_jobs, verify_prefixes
    rows = verify_prefixes(missing_jobs(), context())
    assert len(rows) == 4 and sum(r['saved_replies'] for r in rows) == 62
    assert all(r['prompt_hashes_equal'] for r in rows)
