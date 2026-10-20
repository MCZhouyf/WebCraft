import io
import http.client
import json
import urllib.error

import pytest

from arbiter.llm.gateway import CanaryFailed, GatewayClient, GatewayProtocolError, RateLimited, GatewayError


@pytest.fixture
def client(monkeypatch, tmp_path):
    for key, value in {
        "WC_LLM_API_KEY": "test-secret-not-for-network", "WC_LLM_BASE_URL": "https://example.invalid/v1",
        "WC_LLM_MODEL": "glm-5.2", "ARB_LLM_BUDGET": "inf",
    }.items():
        monkeypatch.setenv(key, value)
    return GatewayClient(tmp_path / "ledger.jsonl")


def response(content, model="glm-5.2", finish="stop"):
    return io.BytesIO(json.dumps({
        "model": model, "choices": [{"message": {"content": content}, "finish_reason": finish}],
        "usage": {"prompt_tokens": 10, "completion_tokens": 20},
    }).encode())


@pytest.mark.parametrize("error", [
    http.client.RemoteDisconnected("closed"),
    http.client.IncompleteRead(b"partial"),
    ConnectionResetError("reset"),
])
def test_connection_failures_are_logged_without_secrets(client, monkeypatch, error):
    def transport(*args, **kwargs):
        raise error
    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(GatewayError):
        client.chat("task", [])
    assert client._records()[-1]["error_type"] == type(error).__name__
    assert client._records()[-1]["status"] == "infrastructure_error"
    assert "test-secret" not in client.ledger.read_text()


def test_invalid_canary_latches_and_blocks_all_subsequent_requests(client, monkeypatch):
    calls = []
    def transport(request, **kwargs):
        calls.append(request)
        return response('{"action":"inventory","args":{}}')
    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(CanaryFailed):
        client.validate_canary()
    with pytest.raises(CanaryFailed):
        client.chat("task", [])
    assert len(calls) == 1
    records = client._records()
    assert records[-1]["status"] == "failed"
    assert "test-secret" not in client.ledger.read_text()


def test_periodic_canary_blocks_task_and_survives_restart(client, monkeypatch):
    for _ in range(200):
        client._append({"call_type": "task", "status": "ok", "canary": False})
    calls = []
    def transport(request, **kwargs):
        calls.append(json.loads(request.data))
        return response("{}")
    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(CanaryFailed):
        client.chat("task", [])
    assert len(calls) == 1
    second = GatewayClient(client.ledger)
    with pytest.raises(CanaryFailed):
        second.chat("task", [])
    assert len(calls) == 1


def test_period_counts_task_calls_not_canary_records(client, monkeypatch):
    client._append({"record_type": "canary_validation", "status": "passed", "canary": True})
    for _ in range(200):
        client._append({"call_type": "task", "status": "ok", "canary": False})
    calls = []
    def transport(request, **kwargs):
        calls.append(request)
        return response('{"action":"inventory","args":{},"intent":"commit"}')
    monkeypatch.setattr("urllib.request.urlopen", transport)
    client.chat("task", [])
    client.chat("task", [])
    assert len(calls) == 3  # One check, then two actual task requests.


def test_model_mismatch_is_logged_and_blocks_further_calls(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response("{}", model="other-glm-5.2"))
    with pytest.raises(GatewayProtocolError):
        client.chat("task", [])
    assert client._records()[-1]["status"] == "model_mismatch"
    with pytest.raises(CanaryFailed):
        client.chat("task", [])


def test_truncated_exact_canary_is_rejected(client, monkeypatch):
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response(
        '{"action":"inventory","args":{},"intent":"commit"}', finish="length"))
    with pytest.raises(CanaryFailed):
        client.validate_canary()


def test_explicit_thinking_mode_is_sent_to_canary_and_logged(client, monkeypatch):
    def transport(request, **kwargs):
        assert json.loads(request.data)["thinking"] == {"type": "disabled"}
        return response('{"action":"inventory","args":{},"intent":"commit"}')
    monkeypatch.setattr("urllib.request.urlopen", transport)
    client.validate_canary(thinking="disabled")
    assert client._records()[-2]["thinking"] == "disabled"


def test_429_is_not_retried_and_retry_after_is_preserved(client, monkeypatch):
    calls = []
    def transport(*args, **kwargs):
        calls.append(True)
        raise urllib.error.HTTPError("https://example.invalid", 429, "limited",
                                     {"Retry-After": "60"}, None)
    monkeypatch.setattr("urllib.request.urlopen", transport)
    from arbiter.llm.gateway import GatewayError
    with pytest.raises(GatewayError):
        client.chat("task", [])
    assert len(calls) == 1
    assert client._records()[-1]["http_status"] == 429
    assert client._records()[-1]["retry_after_seconds"] == 60


def test_requests_wait_between_completions_and_new_calls(client, monkeypatch):
    client.min_interval_seconds = 10
    client._last_finished = 100
    sleeps = []
    monkeypatch.setattr("time.monotonic", lambda: 102)
    monkeypatch.setattr("time.sleep", sleeps.append)
    monkeypatch.setattr("urllib.request.urlopen", lambda *a, **k: response("{}"))
    client.chat("task", [])
    assert sleeps == [8]


def test_429_switch_requires_canary_before_replaying_same_request(client, monkeypatch):
    monkeypatch.setenv("WC_LLM_API_KEYS", json.dumps(["pool-test-one", "pool-test-two"]))
    pool = GatewayClient(client.ledger)
    requests = []
    def transport(request, **kwargs):
        requests.append((request.get_header("Authorization"), json.loads(request.data)))
        if len(requests) == 1:
            raise urllib.error.HTTPError("https://example.invalid", 429, "limited", {}, None)
        if len(requests) == 2:
            return response('{"action":"inventory","args":{},"intent":"commit"}')
        return response('{"action":"submit","args":{"feasible":null},"intent":"commit"}')
    monkeypatch.setattr("urllib.request.urlopen", transport)
    result = pool.chat("task", [{"role": "user", "content": "Choose a public tool."}], thinking="disabled")
    assert result["model"] == "glm-5.2"
    assert [auth for auth, _ in requests] == ["Bearer pool-test-one", "Bearer pool-test-two", "Bearer pool-test-two"]
    assert requests[0][1] == requests[2][1]
    assert "exact JSON" in requests[1][1]["messages"][0]["content"]
    ledger = pool.ledger.read_text()
    assert "pool-test-one" not in ledger and "pool-test-two" not in ledger
    assert any(row.get("record_type") == "credential_switch" for row in pool._records())


def test_all_throttled_keys_are_tried_once_and_pool_stops(client, monkeypatch):
    monkeypatch.setenv("WC_LLM_API_KEYS", json.dumps(["one", "two", "three"]))
    pool = GatewayClient(client.ledger)
    requests = []
    def transport(request, **kwargs):
        requests.append(request.get_header("Authorization"))
        raise urllib.error.HTTPError("https://example.invalid", 429, "limited", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(GatewayError):
        pool.chat("task", [])
    assert requests == ["Bearer one", "Bearer two", "Bearer three"]
    with pytest.raises(CanaryFailed):
        pool.chat("task", [])
    assert len(requests) == 3


def test_failed_canary_on_new_key_blocks_task_without_trying_more_keys(client, monkeypatch):
    monkeypatch.setenv("WC_LLM_API_KEYS", json.dumps(["one", "two", "three"]))
    pool = GatewayClient(client.ledger)
    requests = []
    def transport(request, **kwargs):
        requests.append(request.get_header("Authorization"))
        if len(requests) == 1:
            raise urllib.error.HTTPError("https://example.invalid", 429, "limited", {}, None)
        return response("{}")
    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(CanaryFailed):
        pool.chat("task", [])
    assert requests == ["Bearer one", "Bearer two"]


def test_authentication_error_does_not_trigger_rate_limit_rotation(client, monkeypatch):
    monkeypatch.setenv("WC_LLM_API_KEYS", json.dumps(["one", "two"]))
    pool = GatewayClient(client.ledger)
    calls = []
    def transport(*args, **kwargs):
        calls.append(True)
        raise urllib.error.HTTPError("https://example.invalid", 401, "unauthorized", {}, None)
    monkeypatch.setattr("urllib.request.urlopen", transport)
    with pytest.raises(GatewayError):
        pool.chat("task", [])
    assert len(calls) == 1
