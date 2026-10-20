from arbiter.evaluation.post_review_measurement import (
    external_self_reported, pipeline_plan_source, stable_identification_cost, terminal,
)


def event(method, intent="commit", success=True, primary=1, params=None):
    return {"method": method, "intent": intent, "success": success,
            "params": params or {}, "cost": {"primary": primary}}


def test_plan_source_uses_execution_provenance_and_matches_main_metric():
    record = {"world": "new", "events": [event("mine", "probe"), event("mine")],
              "used_mechanism_ids": ["old", "old"],
              "metrics": {"HTR_all": 1, "HTR_commit": 1}}
    vectors = {"old": {"old": True, "new": False}}
    result, detail = pipeline_plan_source(record, vectors)
    assert result["HTR_commit"] == 1
    assert result["degenerate_first_use_HTR_commit"] == 0
    assert [row["source"] for row in detail] == ["validation_routine", "selected_plan_step"]


def test_pipeline_stale_abstention_is_not_htr():
    record = {"world": "new", "events": [event("submit", primary=0)],
              "used_mechanism_ids": [], "metrics": {"HTR_all": 0, "HTR_commit": 0}}
    result, _ = pipeline_plan_source(record, {"old": {"old": True, "new": False}})
    assert result["HTR_commit"] == 0


def test_external_probe_then_commit_consistency_violation():
    conflict = ({"id": "claim", "assertion": {"kind": "drop", "block": "ore"},
                 "truth_vector": {"old": True, "new": False}},)
    record = {"world": "new", "metrics": {"HTR_all": 1, "HTR_commit": 0,
                                             "self_reported_HTR_commit": 0}}
    result, detail = external_self_reported(
        record, [event("mine", "probe", params={"nodeId": "n"}),
                 event("mine", "commit", params={"nodeId": "n"})],
        conflict, {"n": "ore"},
    )
    assert result["consistency_violation"] == 1
    assert result["stale_consistency_violation"] == 1
    assert detail[0]["later_committed_claim_ids"] == ["claim"]


def test_terminal_priority_and_stable_ic():
    solved = {"metrics": {"SR": 1, "primary_cost": 90}, "true_feasible": True,
              "events": [event("mine", success=False)], "reported_feasible": True}
    assert terminal(solved) == "solved"
    failed = {"metrics": {"SR": 0, "primary_cost": 1}, "true_feasible": True,
              "events": [event("mine", success=False)], "reported_feasible": None}
    assert terminal(failed) == "execution_failed"
    reports = [event("report_version", primary=2, params={"guess_sha256": "right"}),
               event("mine", primary=3)]
    assert stable_identification_cost(reports, "right") == ("identified", 2.0)
