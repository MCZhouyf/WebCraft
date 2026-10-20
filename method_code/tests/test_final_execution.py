from collections import Counter

import numpy as np

from arbiter.agent.decision import ContextBelief, DecisionController
from arbiter.agent import (
    ARBITER_EXECUTION, NAIVE_EXECUTION,
    ArbiterAgent, ExecutionPolicy, classification_information_value,
)
from arbiter.agent.planning import BeliefPlanner, MechanismOperator, action_signature
from arbiter.contracts import Goal
from arbiter.evaluation.p6_execution import _execution_policy


def test_frozen_final_execution_policy():
    assert _execution_policy() == ExecutionPolicy(
        n_plans=5,
        uncertainty_weight=.35,
        inventory_aware=True,
        max_probes=12,
        max_actions=90,
        decision_error_cost=10,
    )


def test_inventory_resolution_uses_available_family_members_and_quantities():
    operator = MechanismOperator(
        "family", "craft_grid", {}, (("a", "b"), ("a", "b")), "goal",
        layout_slots=(0, 1), probability=.9,
    )
    plans = BeliefPlanner([operator], Counter(a=1, b=1), inventory_aware=True).candidates("goal", 1, 1)
    assert plans[0].steps[0].params["layout"][:2] == ["a", "b"]


def test_same_failed_concrete_action_is_not_retried_under_another_claim_id():
    operators = [MechanismOperator(key, "mine", {"node_type": "stone"}, (), "goal")
                 for key in ("first", "second")]
    blocked = frozenset({action_signature("mine", {"node_type": "stone"}, "goal")})
    assert BeliefPlanner(operators, Counter(), blocked_actions=blocked).candidates("goal", 1) == []


def test_voi_does_not_probe_unavailable_or_already_observed_mechanisms():
    operators = [MechanismOperator(key, "mine", {}, (), "goal") for key in ("a", "b")]
    plans = BeliefPlanner(operators, Counter()).candidates("goal", 1, 2)
    belief = ContextBelief(np.array([1.0]), {key: np.array([.6]) for key in ("a", "b")})
    controller = DecisionController()
    assert controller.choose(plans, belief, {"a": 1, "b": 1}, probe_candidates=set()).kind == "execute"
    belief.update("a", False)
    assert controller.choose(plans, belief, {"a": 1, "b": 1}, probe_candidates={"a"}).kind == "execute"


class RecordingAPI:
    def __init__(self, outcomes):
        self.outcomes = outcomes
        self.items = Counter()
        self.calls = []

    def inventory(self):
        return [{"name": name, "count": count} for name, count in self.items.items()]

    def mine(self, name, *, intent):
        self.calls.append((name, intent))
        result = self.outcomes[name]
        for item in result.get("items", []):
            self.items[item["name"]] += item["count"]
        return result

    def submit(self, result):
        self.report = result


def test_replanning_stops_retries_of_equivalent_claims_and_records_intent_before_call():
    api = RecordingAPI({"stone": {"success": False}})
    agent = ArbiterAgent(Goal("goal", 1), [], api)
    operators = [MechanismOperator(key, "mine", {"node_type": "stone"}, (), "goal")
                 for key in ("a", "b")]
    belief = ContextBelief(np.array([1.0]), {key: np.array([.6]) for key in ("a", "b")})
    agent.configure(operators, belief)
    result = agent.solve(policy=ExecutionPolicy())
    assert len(api.calls) == 1
    assert result["policy_trace"][0]["intent"] == api.calls[0][1]
    assert result["feasible"] is False


def test_resource_failure_does_not_update_context_belief():
    api = RecordingAPI({"stone": {"success": False, "error": "missing ingredient"}})
    agent = ArbiterAgent(Goal("goal", 1), [], api)
    operator = MechanismOperator("a", "mine", {"node_type": "stone"}, (), "goal")
    belief = ContextBelief(np.array([.5, .5]), {"a": np.array([.8, .2])})
    agent.configure([operator], belief)
    result = agent.solve(policy=ExecutionPolicy())
    np.testing.assert_array_equal(belief.q, [.5, .5])
    assert belief.observations == {}
    assert result["policy_trace"][0]["resource_failure"]


def test_risk_voi_has_positive_value_for_single_uncertain_route_but_not_certain_route():
    assert classification_information_value(.84, 10, 1) > 0
    assert classification_information_value(.99, 10, 1) < 0
    api = RecordingAPI({"stone": {"success": False}})
    agent = ArbiterAgent(Goal("goal", 1), [], api)
    operator = MechanismOperator("a", "mine", {"node_type": "stone"}, (), "goal")
    agent.configure([operator], ContextBelief(np.array([1.0]), {"a": np.array([.84])}))
    result = agent.solve(policy=ExecutionPolicy(decision_error_cost=10))
    assert api.calls == [("stone", "probe")]
    assert result["policy_trace"][0]["risk_information_value"] > 0


def test_risk_voi_obeys_probe_budget_and_keeps_certain_actions_committed():
    for probability, cap in ((.84, 0), (.99, 4)):
        api = RecordingAPI({"stone": {"success": True, "items": [{"name": "goal", "count": 1}]}})
        agent = ArbiterAgent(Goal("goal", 1), [], api)
        operator = MechanismOperator("a", "mine", {"node_type": "stone"}, (), "goal")
        agent.configure([operator], ContextBelief(np.array([1.0]), {"a": np.array([probability])}))
        assert agent.solve(policy=ExecutionPolicy(decision_error_cost=10, max_probes=cap))["success"]
        assert api.calls == [("stone", "commit")]


def test_naive_and_arbiter_use_the_same_replanning_shell():
    operators = [MechanismOperator(key, "mine", {"node_type": node}, (), "goal")
                 for key, node in (("bad", "stone"), ("good", "dirt"))]
    outcomes = {
        "stone": {"success": False},
        "dirt": {"success": True, "items": [{"name": "goal", "count": 1}]},
    }
    calls = []
    for strategy in (NAIVE_EXECUTION, ARBITER_EXECUTION):
        api = RecordingAPI(outcomes)
        agent = ArbiterAgent(Goal("goal", 1), [], api)
        belief = ContextBelief(np.array([1.0]), {
            "bad": np.array([.99]), "good": np.array([.9]),
        })
        agent.configure(operators, belief)
        result = agent.solve(
            policy=ExecutionPolicy(decision_error_cost=0), strategy=strategy,
        )
        assert result["success"]
        calls.append(api.calls)
    assert calls[0] == calls[1] == [("stone", "commit"), ("dirt", "commit")]


def test_naive_shell_never_probes_or_updates_belief():
    api = RecordingAPI({"stone": {"success": False}})
    agent = ArbiterAgent(Goal("goal", 1), [], api)
    belief = ContextBelief(np.array([.5, .5]), {"a": np.array([.8, .2])})
    agent.configure([
        MechanismOperator("a", "mine", {"node_type": "stone"}, (), "goal"),
    ], belief)
    result = agent.solve(strategy=NAIVE_EXECUTION)
    np.testing.assert_array_equal(belief.q, [.5, .5])
    assert belief.observations == {}
    assert result["probe_mechanism_ids"] == []
    assert api.calls == [("stone", "commit")]
