from collections import Counter

import numpy as np

from arbiter.agent.decision import ContextBelief, DecisionController
from arbiter.agent.planning import BeliefPlanner, MechanismOperator


def test_belief_planner_builds_chain():
    operators = [
        MechanismOperator("mine-log", "mine", {"node_type": "oak_log"}, (), "oak_log", 1, probability=.9),
        MechanismOperator("planks", "craft_grid", {}, (("oak_log",),), "oak_planks", 4,
                          probability=.9, layout_slots=(0,)),
        MechanismOperator("sticks", "craft_grid", {}, (("oak_planks",), ("oak_planks",)), "stick", 4,
                          probability=.9, layout_slots=(0, 3)),
    ]
    plan = BeliefPlanner(operators, Counter()).candidates("stick", 4, 1)[0]
    assert [step.expected_output for step in plan.steps] == ["oak_log", "oak_planks", "stick"]


def test_context_joint_plan_probability():
    operators = [MechanismOperator("a", "mine", {}, (), "x")]
    plan = BeliefPlanner(operators, Counter()).candidates("x", 1, 1)[0]
    belief = ContextBelief(np.array([.25, .75]), {"a": np.array([.9, .1])})
    assert abs(belief.plan_success(plan) - .3) < 1e-9


def test_controller_executes_certain_plan_without_probe():
    plan = BeliefPlanner(
        [MechanismOperator("a", "mine", {}, (), "x")], Counter()
    ).candidates("x", 1, 1)[0]
    belief = ContextBelief(np.array([1.0]), {"a": np.array([.99])})
    assert DecisionController().choose([plan], belief, {"a": 1}).kind == "execute"


def test_controller_values_observing_one_of_two_routes():
    plans = BeliefPlanner([
        MechanismOperator("a", "mine", {}, (), "x", probability=.6),
        MechanismOperator("b", "mine", {}, (), "x", probability=.6),
    ], Counter()).candidates("x", 1, 2)
    belief = ContextBelief(
        np.array([1.0]), {"a": np.array([.6]), "b": np.array([.6])},
    )
    assert DecisionController().choose(plans, belief, {"a": 1, "b": 1}).kind == "probe"


def test_failed_observation_removes_mechanism():
    operator = MechanismOperator("a", "mine", {}, (), "x", probability=.99)
    belief = ContextBelief(np.array([1.0]), {"a": np.array([.99])})
    belief.update("a", False)
    rejected = MechanismOperator(**{**operator.__dict__, "probability": belief.mechanism_probability("a")})
    assert BeliefPlanner([rejected], Counter()).candidates("x", 1, 1) == []


def test_no_good_cut_returns_distinct_routes():
    operators = [
        MechanismOperator("a", "mine", {}, (), "x", probability=.9),
        MechanismOperator("b", "mine", {}, (), "x", probability=.8),
    ]
    plans = BeliefPlanner(operators, Counter()).candidates("x", 1, 2)
    assert len(plans) == 2
    assert plans[0].mechanism_ids != plans[1].mechanism_ids


def test_no_good_cut_does_not_pad_one_route_with_redundant_actions():
    operators = [
        MechanismOperator("cheap", "mine", {}, (), "x", probability=.9),
        MechanismOperator("alternate", "mine", {}, (), "x", probability=.8),
    ]
    plans = BeliefPlanner(operators, Counter()).candidates("x", 1, 5)
    supports = [plan.mechanism_ids for plan in plans]
    assert supports[:2] == [("cheap",), ("alternate",)]
    assert len(supports) == len(set(supports))
    assert [plan.primary_cost for plan in plans[:2]] == [1, 1]


def test_majority_control_uses_shared_belief_shape():
    belief = ContextBelief.majority({"a": [True, True, False], "b": [False, False, True]})
    assert belief.mechanism_probability("a") == .99
    assert belief.mechanism_probability("b") == .01
