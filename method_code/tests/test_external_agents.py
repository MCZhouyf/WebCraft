import json

import pytest

from arbiter.evaluation.external_agents import (
    EXTERNAL_METHODS, ModelOutputError, _parse, public_system_prompt, validate_external_canary,
    run_external_episode,
)
from arbiter.llm import GatewayError
from arbiter.llm.gateway import CanaryFailed


def test_external_prompts_expose_no_hidden_interface_or_revision_labels():
    banned = ("report_version", "truth", "candidate", "1.16.5", "1.19", "1.21.11")
    for method in EXTERNAL_METHODS:
        prompt = public_system_prompt(method)
        assert all(value not in prompt for value in banned)


def test_external_action_parser_rejects_extra_or_hidden_actions():
    assert _parse('{"action":"inventory","args":{},"intent":"commit"}')["action"] == "inventory"
    assert _parse('{"action":"inventory","args":{},"intent":"commit","thought":"check inventory"}')["action"] == "inventory"
    with pytest.raises(ModelOutputError):
        _parse('{"action":"report_version","args":{},"intent":"commit"}')
    with pytest.raises(ModelOutputError):
        _parse('{"action":"inventory","args":{},"intent":"commit","extra":1}')


def test_external_canary_requires_one_safe_action():
    validate_external_canary('{"action":"inventory","args":{},"intent":"commit"}')
    with pytest.raises(GatewayError):
        validate_external_canary('{"action":"search","args":{"query":"x","k":1},"intent":"commit"}')


@pytest.mark.parametrize("action", [
    {"action": [], "args": {}, "intent": "commit"},
    {"action": "submit", "args": {"feasible": []}, "intent": "commit"},
    {"action": "submit", "args": {"feasible": 1}, "intent": "commit"},
    {"action": "submit", "args": {}, "intent": "commit"},
    {"action": "inventory", "args": {}, "intent": []},
    {"action": "search", "args": {"query": "recipe", "k": True}, "intent": "commit"},
    {"action": "smelt", "args": {"input_item": "ore", "fuel": "coal", "n": True}, "intent": "commit"},
])
def test_malformed_outputs_have_safe_error_codes(action):
    with pytest.raises(ModelOutputError):
        _parse(json.dumps(action))


class FakeAPI:
    def __init__(self):
        self.executed = []

    def inventory(self, **kwargs):
        return []

    def submit(self, result, **kwargs):
        self.executed.append(result)
        return {"success": True}


TASK = {"target": {"item": "paper", "count": 1}, "standard_items": [],
        "feasible": {"SECRET_WORLD": True}, "conflict_ids": ["SECRET_CONFLICT"]}


class Replies:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.calls = 0

    def chat(self, *args, **kwargs):
        assert "SECRET_" not in json.dumps(args)
        self.calls += 1
        reply = next(self.replies)
        if isinstance(reply, Exception):
            raise reply
        return {"content": reply, "model": "glm-5.2", "usage": {}, "finish_reason": "stop"}


def test_mid_episode_canary_failure_propagates_without_retry_or_submit():
    api = FakeAPI()
    client = Replies([CanaryFailed("canary_validation_failed")])
    with pytest.raises(CanaryFailed):
        run_external_episode(api, client, method="direct_llm", task=TASK)
    assert client.calls == 1
    assert api.executed == []


def test_tool_error_is_diagnosed_and_recovers_without_hidden_fields():
    api = FakeAPI()
    client = Replies(['{"action":[]}', '{"action":"submit","args":{"feasible":null},"intent":"commit"}'])
    episode = run_external_episode(api, client, method="direct_llm", task=TASK)
    assert episode.model_calls == 2
    assert episode.diagnostics[0]["error_code"] == "invalid_top_level_fields"
    assert api.executed == [{"feasible": None}]


def test_smelt_cannot_exceed_primary_action_budget():
    api = FakeAPI()
    client = Replies(['{"action":"smelt","args":{"input_item":"ore","fuel":"coal","n":91},"intent":"commit"}'])
    episode = run_external_episode(api, client, method="direct_llm", task=TASK)
    assert episode.primary_cost == 0
    assert episode.diagnostics[0]["error_code"] == "action_budget_exceeded"


def test_three_consecutive_bad_outputs_stop_without_submit():
    api = FakeAPI()
    client = Replies(["", "", ""])
    episode = run_external_episode(api, client, method="direct_llm", task=TASK)
    assert episode.model_output_error == "empty_content"
    assert client.calls == 3
    assert api.executed == []
