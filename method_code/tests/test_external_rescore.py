import pytest

from arbiter.evaluation.external_rescore import action_audit, clustered_ratio, expected_charge, outcome, paired_difference


def event(method, *, params=None, result=None, success=True, cost=0, intent="commit"):
    return dict(method=method, params=params or {}, result=result, success=success,
                cost={"primary": cost}, intent=intent)


def episode(*, feasible=True, reported=True, attained=True, calls=1):
    task = {"feasible": {"world": feasible}, "target": {"item": "goal", "count": 1}}
    events = [event("mine", success=False, cost=1),
              event("submit", params={"result": {"feasible": reported}}),
              event("inventory", result=[{"name": "goal", "count": int(attained)}])]
    row = {"world": "world", "true_feasible": feasible, "reported_feasible": reported,
           "metrics": {"SR": int((feasible and reported is True and attained) or (not feasible and reported is False))},
           "model_calls": calls}
    return row, events, task


def test_failure_before_success_is_not_terminal_failure():
    r = outcome(*episode())
    assert r["terminal"] == "solved"
    assert r["any_action_failure"] == 1
    assert r["terminal_execution_failure"] == 0


@pytest.mark.parametrize("feasible,reported,attained,calls,terminal", [
    (False, False, False, 1, "correct_infeasible"),
    (True, False, False, 40, "false_infeasible"),
    (True, None, False, 40, "budget_exhausted"),
    (True, None, False, 1, "execution_failed"),
])
def test_terminal_precedence(feasible, reported, attained, calls, terminal):
    assert outcome(*episode(feasible=feasible, reported=reported, attained=attained,
                            calls=calls))["terminal"] == terminal


def test_missing_terminal_evidence_fails_closed():
    row, events, task = episode()
    with pytest.raises(ValueError):
        outcome(row, events[:-1], task)


def test_probe_labels_do_not_identify_behavioral_commit():
    conflicts = ({"id": "old", "assertion": {"kind": "drop", "block": "ore"},
                  "truth_vector": {"world": False, "other": True}},)
    row = {"world": "world", "metrics": {"HTR_all": 1, "HTR_commit": 0}}
    events = [event("search", intent="probe"), event("inventory", intent="probe"),
              event("mine", params={"nodeId": "node"}, cost=1)]
    metrics, audit = action_audit(row, events, conflicts, {"node": "ore"})
    assert metrics["behavioral_HTR_commit"] is None
    assert metrics["behavioral_HTR_commit_upper"] == 1
    assert metrics["labelled_probe_count"] == 2
    assert metrics["world_probe_count"] == 0
    assert audit[0]["reason"] == "no_independent_plan_dependency_witness"


def test_recorded_operator_disambiguates_contradictory_drop_candidates():
    conflicts = tuple({"id": i, "assertion": {"kind": "drop", "block": "ore"},
                       "truth_vector": {"world": truth, "other": not truth}}
                      for i, truth in (("old", False), ("new", True)))
    row = {"world": "world", "used_mechanism_ids": ["new"],
           "metrics": {"HTR_all": 0, "HTR_commit": 0}}
    metrics, _ = action_audit(row, [event("mine", params={"nodeId": "node"}, cost=1)], conflicts, {"node": "ore"})
    assert metrics["behavioral_HTR_commit"] == 0


def test_smelt_failed_requested_units_still_charged():
    assert expected_charge(event("smelt", params={"n": 4}, success=False)) == 4
    assert expected_charge(event("smelt", params={"n": True})) == 1


def test_conditional_denominator_and_joint_task_bootstrap():
    rows = [{"task_id": "a", "abstain": 1, "feasible": 1},
            {"task_id": "b", "abstain": 0, "feasible": 0}]
    result = clustered_ratio(rows, "abstain", "feasible", draws=100)
    assert result["estimate"] == 1
    assert result["zero_denominator_draws"] > 0
    paired = [{"task_id": str(i), "condition": c, "world": "world", "true_feasible": 1, "SR": i % 2}
              for i in range(22) for c in ("a", "b")]
    ci = paired_difference(paired, paired, "SR")
    assert ci["clusters"] == 22 and ci["low"] == ci["high"] == 0
    with pytest.raises(ValueError):
        paired_difference(paired, paired[:-1], "SR")
