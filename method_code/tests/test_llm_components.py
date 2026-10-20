import json

import pytest

from arbiter.llm import BudgetExceeded, CachedClaimExtractor, DirectPlanner, GatewayClient, GatewayError
from arbiter.llm.components import CACHE_FALLBACK_PROMPT, retrieval_queries


def test_retrieval_queries_are_goal_derived():
    queries = retrieval_queries("raw_iron")
    assert len(queries) == 3
    assert all("raw iron" in query for query in queries)
    assert queries[0] == "raw iron"


class FakeClient:
    def __init__(self, remaining):
        self.remaining = remaining
        self.calls = 0

    def remaining_budget(self):
        return self.remaining

    def chat(self, *args, **kwargs):
        self.calls += 1
        return {"content": json.dumps([
            {"method": "mine", "params": {"node_type": "oak_log"}},
        ])}


class FakeClaimClient:
    def chat(self, *args, **kwargs):
        assert kwargs["temperature"] == 0.0
        return {"content": json.dumps({"chunks": [{
            "source_key": "oak-1",
            "claims": [{
                "claim_type": "drop", "subject_item": "oak_log",
                "payload": {"drops": ["oak_log"], "silk_touch": False},
                "polarity": "positive", "tense": "current",
                "span_text": "Oak logs drop oak log.",
            }],
        }]})}


def public_hit():
    return {
        "text": "Oak logs drop oak log.", "source_site": "wiki",
        "page_title": "Oak", "revid": "1", "timestamp": "2025-01-01",
        "chunk_id": "oak-1",
    }


def test_llm_direct_budget_gate_makes_no_call():
    client = FakeClient(29.99)
    with pytest.raises(BudgetExceeded):
        DirectPlanner(client).plan([public_hit()], {"item": "oak_log", "count": 1})
    assert client.calls == 0


def test_llm_direct_parses_actions_without_real_gateway():
    client = FakeClient(30.0)
    actions = DirectPlanner(client).plan([public_hit()], {"item": "oak_log", "count": 1})
    assert actions == [{"method": "mine", "params": {"node_type": "oak_log"}}]
    assert client.calls == 1


def test_cache_fallback_is_per_chunk_and_deterministic():
    assert all(value not in CACHE_FALLBACK_PROMPT for value in ("1.16.5", "1.19", "1.21.11"))
    first = CachedClaimExtractor(FakeClaimClient()).extract(public_hit())
    second = CachedClaimExtractor(FakeClaimClient()).extract(public_hit())
    assert first == second
    assert set(first[0]) == {
        "claim_id", "chunk_id", "claim_type", "subject_item", "payload",
        "polarity", "tense", "span_text", "extractor_version",
        "normalizer_version", "parent_claim_id",
    }
    assert first[0]["normalizer_version"] is None
    assert first[0]["parent_claim_id"] is None


def test_paid_gateway_requires_nonzero_pricing(monkeypatch, tmp_path):
    monkeypatch.setenv("WC_LLM_API_KEY", "test-key")
    monkeypatch.setenv("WC_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("WC_LLM_MODEL", "glm-5.2")
    monkeypatch.setenv("ARB_LLM_BUDGET", "10")
    monkeypatch.delenv("WC_LLM_PRICE_IN_PER_MTOK", raising=False)
    monkeypatch.delenv("WC_LLM_PRICE_OUT_PER_MTOK", raising=False)
    client = GatewayClient(tmp_path / "ledger.jsonl")
    with pytest.raises(GatewayError, match="PRICE"):
        client.require_priced_budget()


def test_unpriced_records_do_not_become_zero_cost_spend(monkeypatch, tmp_path):
    monkeypatch.setenv("WC_LLM_API_KEY", "test-key")
    monkeypatch.setenv("WC_LLM_BASE_URL", "https://example.invalid/v1")
    monkeypatch.setenv("WC_LLM_MODEL", "glm-5.2")
    monkeypatch.setenv("ARB_LLM_BUDGET", "inf")
    ledger = tmp_path / "ledger.jsonl"
    ledger.write_text(json.dumps({"status": "ok", "cost": None}) + "\n")
    assert GatewayClient(ledger).spent() == 0.0
