from arbiter.evaluation.external_scoring import score_external_trace


def _event(method, params, *, intent="commit", success=True, primary=1):
    return {
        "schema_version": 2, "method": method, "params": params,
        "intent": intent, "success": success,
        "cost": {"primary": primary},
    }


def test_external_scoring_is_evaluator_only_and_preserves_behavioral_rule():
    conflicts = ({
        "id": "old_name", "assertion": {"kind": "name", "item": "new_item", "exists": True},
        "truth_vector": {"1.16.5": False, "1.19": True, "1.21.11": True},
    },)
    metrics = score_external_trace(
        [_event("check_name", {"name": "new_item"})], world="1.16.5",
        conflicts=conflicts, resource_nodes={}, true_feasible=True, reported_feasible=False,
    )
    assert metrics["HTR_all"] == 1
    assert metrics["HTR_commit"] == 0
    assert metrics["self_reported_HTR_commit"] == 1
    assert metrics["intent_label_disagreements"] == 1
    assert metrics["false_abstention"] is True
    assert metrics["execution_failure"] is False


def test_external_scoring_matches_craft_and_resource_drop():
    conflicts = (
        {
            "id": "old_recipe",
            "assertion": {
                "kind": "recipe", "station": "crafting_grid", "ordered": True,
                "grid": [[["stone"]], [["stick"]]], "output": "tool", "count": 1,
            },
            "truth_vector": {"1.16.5": True, "1.19": False, "1.21.11": False},
        },
        {
            "id": "old_drop",
            "assertion": {"kind": "drop", "block": "iron_ore", "drops": ["iron_ore"]},
            "truth_vector": {"1.16.5": True, "1.19": False, "1.21.11": False},
        },
    )
    layout = ["stone", None, None, "stick", None, None, None, None, None]
    events = [
        _event("craft_grid", {"layout": layout}, success=False),
        _event("mine", {"nodeId": "iron_1"}, intent="probe", success=True),
        _event("search", {"q": "iron", "k": 5}, primary=0),
    ]
    metrics = score_external_trace(
        events, world="1.21.11", conflicts=conflicts,
        resource_nodes={"iron_1": "iron_ore"}, true_feasible=True, reported_feasible=None,
    )
    assert metrics["HTR_all"] == 1
    assert metrics["execution_failure"] is True
    assert metrics["probe_count"] == 1
    assert metrics["probe_cost"] == 1
    assert metrics["search_count"] == 1
    assert {row["mechanism_ids"][0] for row in metrics["matched_DV_actions"]} == {"old_recipe", "old_drop"}
