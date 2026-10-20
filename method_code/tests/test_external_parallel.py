import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from run_external_parallel import (
    _chunk_plan, _chunks, _credentials, _endpoint_configs, _merge_rows,
    _rate_limit_retry_after, _rate_limited,
    _rotate_retry_credentials, _transport_interrupted,
)


def test_parallel_chunks_cover_fixed_interval_once():
    chunks = _chunks(10, 100, 4)
    assert chunks == [(10, 25), (35, 25), (60, 25), (85, 25)]
    covered = [job for offset, count in chunks for job in range(offset, offset + count)]
    assert covered == list(range(10, 110))


def test_parallel_chunks_handle_uneven_and_small_runs():
    assert _chunks(7, 3, 4) == [(7, 1), (8, 1), (9, 1)]
    assert _chunks(0, 10, 4) == [(0, 3), (3, 3), (6, 3), (9, 1)]


def test_continuation_chunk_plan_is_nonoverlapping_and_hashed(tmp_path):
    path = tmp_path / "plan.json"
    path.write_text(json.dumps({
        "schema_version": 1, "reason": "transport_stop", "source_run": "run",
        "chunks": [{"offset": 3, "count": 25}, {"offset": 31, "count": 25}],
    }))
    chunks, provenance = _chunk_plan(path)
    assert chunks == [(3, 25), (31, 25)]
    assert len(provenance["sha256"]) == 64
    path.write_text(json.dumps({
        "schema_version": 1,
        "chunks": [{"offset": 3, "count": 25}, {"offset": 20, "count": 3}],
    }))
    with pytest.raises(ValueError):
        _chunk_plan(path)


def test_parallel_credentials_are_deduplicated_without_logging(monkeypatch):
    monkeypatch.setenv("WC_LLM_API_KEYS", json.dumps(["secret-a", "secret-b", "secret-a"]))
    assert _credentials() == ["secret-a", "secret-b"]


def test_parallel_endpoints_support_exact_per_credential_models(monkeypatch):
    monkeypatch.setenv("WC_LLM_ENDPOINTS", json.dumps([
        {"base_url": "https://one.example/v1", "model": "glm-5.2"},
        {"base_url": "https://two.example/v1/", "model": "zai-org/GLM-5.2"},
    ]))
    assert _endpoint_configs(2) == [
        {"base_url": "https://one.example/v1", "model": "glm-5.2"},
        {"base_url": "https://two.example/v1", "model": "zai-org/GLM-5.2"},
    ]
    with pytest.raises(RuntimeError):
        _endpoint_configs(1)


@pytest.mark.parametrize("offset,limit,workers", [(-1, 1, 1), (0, 0, 1), (0, 1, 0)])
def test_parallel_chunk_arguments_are_validated(offset, limit, workers):
    with pytest.raises(ValueError):
        _chunks(offset, limit, workers)


def test_parallel_merge_requires_exact_manifest_jobs(tmp_path):
    attempts = []
    for index, task_id in enumerate(("a", "b"), 1):
        worker = tmp_path / f"attempt_{index}"
        worker.mkdir(); attempts.append(worker)
        job = {"method": "direct_llm", "condition": "matched_1.16.5",
               "world": "1.16.5", "task_id": task_id}
        (worker / "run_manifest.json").write_text(json.dumps({"jobs": [job]}))
        (worker / "episodes.jsonl").write_text(json.dumps({**job, "seed": 20261006}) + "\n")
    rows = _merge_rows(attempts, 2)
    assert [row["task_id"] for row in rows] == ["a", "b"]
    (attempts[1] / "episodes.jsonl").write_text(
        (attempts[0] / "episodes.jsonl").read_text()
    )
    with pytest.raises(RuntimeError):
        _merge_rows(attempts, 2)


def test_parallel_rate_limit_detection_uses_ledger_status_only(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "ledger.jsonl").write_text(json.dumps({"http_status": 429}) + "\n")
    assert _rate_limited(attempt) is True
    (attempt / "ledger.jsonl").write_text(json.dumps({"http_status": 500}) + "\n")
    assert _rate_limited(attempt) is False


def test_rate_limit_retry_after_uses_latest_numeric_hint(tmp_path):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "ledger.jsonl").write_text("\n".join([
        json.dumps({"http_status": 429, "retry_after_seconds": 12}),
        json.dumps({"http_status": 429, "retry_after_seconds": 45.5}),
    ]) + "\n")
    assert _rate_limit_retry_after(attempt) == 45.5
    (attempt / "ledger.jsonl").write_text(
        json.dumps({"http_status": 429, "retry_after_seconds": None}) + "\n"
    )
    assert _rate_limit_retry_after(attempt) is None


@pytest.mark.parametrize(
    "record,expected",
    [
        ({"status": "infrastructure_error", "error_type": "URLError",
          "http_status": None}, True),
        ({"status": "infrastructure_error", "error_type": "TimeoutError",
          "http_status": None}, True),
        ({"status": "infrastructure_error", "error_type": "HTTPError",
          "http_status": 503}, True),
        ({"status": "infrastructure_error", "error_type": "HTTPError",
          "http_status": 400}, False),
        ({"status": "protocol_error", "error_type": "invalid_response_envelope"}, False),
    ],
)
def test_transport_retry_classification_is_narrow(tmp_path, record, expected):
    attempt = tmp_path / "attempt"
    attempt.mkdir()
    (attempt / "ledger.jsonl").write_text(json.dumps(record) + "\n")
    assert _transport_interrupted(attempt) is expected


def test_rate_limited_shards_rotate_to_credentials_freed_by_completed_shards():
    states = [
        {"slot": 1, "credential_index": 0, "remaining": 0},
        {"slot": 2, "credential_index": 1, "remaining": 0},
        {"slot": 3, "credential_index": 2, "remaining": 1},
        {"slot": 4, "credential_index": 3, "remaining": 1},
    ]
    _rotate_retry_credentials(states, {3, 4}, 4)
    assert states[2]["credential_index"] != 2
    assert states[3]["credential_index"] != 3
    assert len({states[2]["credential_index"], states[3]["credential_index"]}) == 2


def test_all_rate_limited_shards_rotate_without_sharing_a_credential():
    states = [
        {"slot": index + 1, "credential_index": index, "remaining": 1}
        for index in range(4)
    ]
    _rotate_retry_credentials(states, {1, 2, 3, 4}, 4)
    assert {state["credential_index"] for state in states} == {0, 1, 2, 3}
    assert all(state["credential_index"] != state["slot"] - 1 for state in states)


def test_disconnect_retry_does_not_mask_failed_canary(tmp_path):
    ledger = tmp_path / "ledger.jsonl"
    disconnected = {"status": "infrastructure_error", "error_type": "RemoteDisconnected"}
    ledger.write_text(json.dumps(disconnected) + "\n")
    assert _transport_interrupted(tmp_path)
    with ledger.open("a") as handle:
        handle.write(json.dumps({"status": "ok", "canary": True}) + "\n")
        handle.write(json.dumps({"record_type": "canary_validation", "status": "failed"}) + "\n")
    assert not _transport_interrupted(tmp_path)
