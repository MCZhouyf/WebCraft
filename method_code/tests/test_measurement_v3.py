from arbiter.evaluation.measurement_v3 import (
    classify_actions_v3, episode_bounds, prerequisite_ready,
)


CONFLICTS = {
    "old_recipe": {
        "id": "old_recipe",
        "assertion": {
            "kind": "recipe", "station": "crafting_grid",
            "output": "old_widget", "ingredients": [["stick"]], "ordered": False,
        },
        "truth_vector": {"old": True, "new": False},
    },
}


def event(method, *, params=None, success=True, produced=None, consumed=None):
    return {
        "method": method, "params": params or {}, "success": success,
        "item_flow": {
            "produced": [{"name": value, "count": 1} for value in produced or []],
            "consumed": [{"name": value, "count": 1} for value in consumed or []],
        },
        "result": {},
    }


def classify(events):
    return classify_actions_v3(
        events, stale_by_event={0: ["old_recipe"]}, conflicts=CONFLICTS,
        resource_nodes={}, target="goal", episode_success=False,
    )


def test_predicted_product_reference_is_commit():
    rows = classify([
        event("craft_grid", params={"layout": ["stick"]}, success=False),
        event("smelt", params={"input": "old_widget"}, consumed=["old_widget"]),
    ])
    assert rows[0]["v3_label"] == "commit"
    assert rows[0]["commit_witnesses"] == ["predicted_product_referenced"]
    assert episode_bounds(rows) == (1, 1)


def test_infeasibility_submission_is_verify_not_commit():
    rows = classify([
        event("craft_grid", params={"layout": ["stick"]}, success=False),
        event("submit", params={"result": {"feasible": False}}),
    ])
    assert rows[0]["v3_label"] == "verify"
    assert rows[0]["commit_witnesses"] == []
    assert rows[0]["verify_witnesses"] == ["infeasible_submission"]
    assert episode_bounds(rows) == (0, 0)


def test_budget_like_end_is_undetermined():
    rows = classify([
        event("craft_grid", params={"layout": ["stick"]}, success=False),
        event("inventory"),
    ])
    assert rows[0]["v3_label"] == "undetermined"
    assert episode_bounds(rows) == (0, 1)


def test_affirmative_submit_without_route_change_is_commit():
    rows = classify([
        event("craft_grid", params={"layout": ["stick"]}, success=False),
        event("submit", params={"result": {"feasible": True}}),
    ])
    assert rows[0]["v3_label"] == "commit"
    assert rows[0]["commit_witnesses"] == ["affirmative_submit_without_route_change"]


def test_missing_input_requires_public_inventory_corroboration():
    inventory = event("inventory")
    inventory["result"] = [{"name": "stick", "count": 1}]
    failed = event("craft_grid", params={"layout": ["diamond"]}, success=False)
    failed["result"] = {"error": "missing ingredient"}
    assert prerequisite_ready([inventory, failed], 1) == "false"
    assert prerequisite_ready([failed], 0) == "unknown"
