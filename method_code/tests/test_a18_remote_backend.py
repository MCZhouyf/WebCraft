"""No network, no credentials, no GPU: remote client safety checks."""
import concurrent.futures
import json
import threading

import pytest

from scripts.a18_remote_backend import Coordinator, RemoteError, WorkerClient, load_private_config


MESSAGES = [{'role': 'user', 'content': 'test'}]
KWARGS = dict(temperature=0.0, max_tokens=4096, thinking='disabled', json_object=True)


class FakeEndpoint:
    model = 'test-service-model'

    def __init__(self, *, mismatch=False, error=False, answer=None):
        self.payloads = []
        self.mismatch, self.error, self.answer = mismatch, error, answer

    def request(self, suffix, payload):
        assert suffix == 'chat/completions'
        self.payloads.append(payload)
        if self.error:
            raise RemoteError('request_or_queue_timeout')
        return {'model': 'wrong-model' if self.mismatch else self.model,
                'choices': [{'message': {'content': self.answer or json.dumps({
                    'action': 'inventory', 'args': {}, 'intent': 'commit'})}, 'finish_reason': 'stop'}],
                'usage': {'prompt_tokens': 7, 'completion_tokens': 11}}


def test_private_config_requires_owner_only_permissions(tmp_path):
    path = tmp_path / 'private.json'
    path.write_text(json.dumps({'model': 'test-model'}))
    path.chmod(0o644)
    with pytest.raises(RemoteError, match='permissions'):
        load_private_config(path)
    path.chmod(0o600)
    assert load_private_config(path)['model'] == 'test-model'


def test_model_mismatch_latches_stop(tmp_path):
    coordinator = Coordinator(FakeEndpoint(mismatch=True), tmp_path)
    with pytest.raises(RemoteError, match='identifier_mismatch'):
        WorkerClient(coordinator).chat('test', MESSAGES, **KWARGS)
    assert coordinator.failed == 'response_model_identifier_mismatch'
    assert coordinator.calls == 0
    with pytest.raises(RemoteError, match='circuit'):
        WorkerClient(coordinator).chat('test', MESSAGES, **KWARGS)


def test_timeout_is_not_retried_or_counted_as_answer(tmp_path):
    endpoint = FakeEndpoint(error=True)
    coordinator = Coordinator(endpoint, tmp_path)
    with pytest.raises(RemoteError, match='timeout'):
        WorkerClient(coordinator).chat('test', MESSAGES, **KWARGS)
    assert len(endpoint.payloads) == 1
    assert coordinator.failed and coordinator.calls == 0


def test_payload_preserves_messages_and_decoding(tmp_path):
    endpoint = FakeEndpoint()
    client = WorkerClient(Coordinator(endpoint, tmp_path))
    client.chat('test', MESSAGES, **KWARGS)
    assert endpoint.payloads[0] == {
        'model': endpoint.model, 'messages': MESSAGES, 'temperature': 0.0, 'max_tokens': 4096,
        'response_format': {'type': 'json_object'}, 'chat_template_kwargs': {'enable_thinking': False}}
    assert (client.task_calls, client.task_input_tokens, client.task_output_tokens) == (1, 7, 11)


def test_canary_failure_stops_all_workers(tmp_path):
    from arbiter.llm.gateway import CanaryFailed
    coordinator = Coordinator(FakeEndpoint(answer='{}'), tmp_path)
    with pytest.raises(CanaryFailed):
        coordinator.canary()
    assert coordinator.failed


def test_global_canary_boundary_with_two_workers(tmp_path):
    endpoint = FakeEndpoint()
    coordinator = Coordinator(endpoint, tmp_path)
    coordinator.next_canary = 4
    # Following boundaries remain the production 200 calls apart.
    barrier = threading.Barrier(2)
    def work():
        client = WorkerClient(coordinator)
        barrier.wait(timeout=5)
        for _ in range(5):
            client.chat('test', MESSAGES, **KWARGS)
        return client.task_calls
    with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(work) for _ in range(2)]
        assert [future.result(timeout=10) for future in futures] == [5, 5]
    assert coordinator.started == coordinator.finished == coordinator.task_calls == 10
    assert coordinator.calls == 11
    assert not coordinator.failed
    rows = [json.loads(line) for line in (tmp_path / 'ledger.jsonl').read_text().splitlines()]
    assert sum(row['canary'] for row in rows) == 1
    assert rows[4]['canary']
