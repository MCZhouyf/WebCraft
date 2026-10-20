"""Synthetic transport tests; no gateway or task execution."""
import json
import urllib.error
from contextlib import nullcontext
from types import SimpleNamespace

import pytest

from scripts import run_a21_mds as runner
from arbiter.llm.gateway import CanaryFailed, GatewayError, GatewayProtocolError, RateLimited


def client(tmp_path, monkeypatch):
    monkeypatch.setenv('WC_LLM_API_KEY', 'test-credential-not-a-secret')
    monkeypatch.delenv('WC_LLM_API_KEYS', raising=False)
    monkeypatch.setenv('WC_LLM_BASE_URL', 'https://example.invalid/v1')
    monkeypatch.setenv('WC_LLM_MODEL', 'glm-5.2')
    monkeypatch.setenv('ARB_LLM_BUDGET', '100')
    value = runner.PooledAuditedClient(tmp_path / 'ledger.jsonl', model='glm-5.2')
    value.wait_on_429 = True
    value.audit_path = tmp_path / 'prompts.jsonl'
    return value


def response(content='{}'):
    return {'model': 'glm-5.2', 'content': content, 'usage': {'prompt_tokens': 1,
            'completion_tokens': 1}, 'finish_reason': 'stop', 'cost': 0.0}


def test_429_retries_same_request_without_extra_decisions(tmp_path, monkeypatch):
    value = client(tmp_path, monkeypatch)
    calls, sleeps = [], []

    def request(self, *args, **kwargs):
        calls.append((args, kwargs))
        if len(calls) <= 5:
            raise RateLimited('http_429')
        return response()

    monkeypatch.setattr(runner.GatewayClient, '_chat_once', request)
    monkeypatch.setattr(runner.time, 'sleep', sleeps.append)
    value.chat('external_react', [{'role': 'user', 'content': 'synthetic'}])
    assert len(calls) == 6 and all(row == calls[0] for row in calls)
    assert sleeps == [60, 120, 240, 300, 300]
    assert value.task_calls == 1 and value.calls == 1
    assert value.rate_limit_streak == 0 and not value._blocked
    assert value._credential_index == 0
    prompts = runner.read_jsonl(value.audit_path)
    assert [row['record_type'] for row in prompts] == ['full_prompt', 'full_response']


def test_canary_429_waits_but_bad_content_still_latches(tmp_path, monkeypatch):
    value = client(tmp_path, monkeypatch)
    calls = []

    def request(self, *args, **kwargs):
        calls.append(args)
        if len(calls) == 1:
            raise RateLimited('http_429')
        return response(json.dumps({'action': 'inventory', 'args': {}, 'intent': 'commit'}))

    monkeypatch.setattr(runner.GatewayClient, '_chat_once', request)
    monkeypatch.setattr(runner.time, 'sleep', lambda delay: None)
    value.validate_canary(thinking='disabled')
    assert not value._blocked and value.task_calls == 0
    monkeypatch.setattr(runner.GatewayClient, '_chat_once', lambda *a, **kw: response('wrong'))
    with pytest.raises(CanaryFailed):
        value.validate_canary(thinking='disabled')
    assert value._blocked


def test_non_429_error_is_not_swallowed(tmp_path, monkeypatch):
    value = client(tmp_path, monkeypatch)

    def request(*args, **kwargs):
        raise GatewayError('synthetic_authorization_error')

    monkeypatch.setattr(runner.GatewayClient, '_chat_once', request)
    with pytest.raises(GatewayError):
        value._chat_once('external_react', [])
    assert value.rate_limit_streak == 0


def test_retry_after_seconds_and_date(monkeypatch):
    error = RateLimited('http_429')
    error.__context__ = urllib.error.HTTPError('https://example.invalid', 429, '',
                                               {'Retry-After': '420'}, None)
    assert runner.retry_after_seconds(error) == 420
    monkeypatch.setattr(runner.time, 'time', lambda: 0)
    error.__context__.headers['Retry-After'] = 'Thu, 01 Jan 1970 00:02:00 GMT'
    assert runner.retry_after_seconds(error) == 120
    error.__context__.headers['Retry-After'] = 'invalid'
    assert runner.retry_after_seconds(error) == 0


def test_peer_cooldown_extends_already_reserved_slot(tmp_path, monkeypatch):
    value = client(tmp_path, monkeypatch)
    clock = SimpleNamespace(value=0.0, get_lock=lambda: nullcontext())
    cooldown = SimpleNamespace(value=60.0, get_lock=lambda: nullcontext())
    value.request_clock, value.cooldown_clock = clock, cooldown
    value.global_request_gap = 20
    now, sleeps, starts = [0.0], [], []
    monkeypatch.setattr(runner.time, 'monotonic', lambda: now[0])

    def sleep(delay):
        sleeps.append(delay)
        now[0] += delay
        if len(sleeps) == 1:
            cooldown.value = 120.0

    def request(*args, **kwargs):
        starts.append(now[0])
        return response()

    monkeypatch.setattr(runner.time, 'sleep', sleep)
    monkeypatch.setattr(runner.GatewayClient, '_chat_once', request)
    value._chat_once('external_react', [])
    assert starts == [120.0]
    assert sleeps == [60.0, 60.0]


def test_real_gateway_path_retries_429_and_keeps_model_lock(tmp_path, monkeypatch):
    from arbiter.llm import gateway

    value = client(tmp_path, monkeypatch)
    payloads, sleeps = [], []

    class Reply:
        model = 'glm-5.2'

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def read(self):
            return json.dumps({'model': self.model, 'choices': [{'message': {'content': '{}'},
                              'finish_reason': 'stop'}], 'usage': {}}).encode()

    def urlopen(request, **kwargs):
        payloads.append(request.data)
        if len(payloads) == 1:
            raise urllib.error.HTTPError('https://example.invalid', 429, '',
                                         {'Retry-After': '90'}, None)
        return Reply()

    monkeypatch.setattr(gateway.urllib.request, 'urlopen', urlopen)
    monkeypatch.setattr(runner.time, 'sleep', sleeps.append)
    value.chat('external_react', [{'role': 'user', 'content': 'synthetic'}])
    assert sleeps == [90]
    assert payloads[0] == payloads[1] and value.task_calls == 1
    assert not value._blocked
    Reply.model = 'wrong-model'
    with pytest.raises(GatewayProtocolError):
        value.chat('external_react', [{'role': 'user', 'content': 'synthetic'}])
    assert value._blocked
