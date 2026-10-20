from arbiter.evaluation.consequence_htr import consequence_actions, episode_htr


def event(method, *, success=True, params=None, consumed=None, produced=None, intent="probe"):
    return {
        "schema_version": 2, "method": method, "params": params or {},
        "success": success, "intent": intent,
        "item_flow": {"consumed": consumed or [], "produced": produced or []},
        "cost": {"primary": 1},
    }


def test_repeated_mismatch_is_commit_even_when_all_actions_claim_probe():
    events = [
        event("craft_grid", success=False, params={"layout": ["old"]}),
        event("craft_grid", success=False, params={"layout": ["old"]}),
    ]
    rows = consequence_actions(
        events, stale_by_event={0: ["cf_old"], 1: ["cf_old"]},
        resource_nodes={}, mismatches={0: True, 1: True},
    )
    assert rows[0]["witnesses"] == ["repeated_after_mismatch"]
    assert episode_htr(rows) == (1, 1)


def test_fifo_requires_consumption_of_the_produced_lot():
    inventory = event("inventory", produced=[])
    inventory["result"] = [{"name": "part", "count": 1}]
    events = [
        inventory,
        event("craft_grid", produced=[{"name": "part", "count": 1}]),
        event("craft_grid", consumed=[{"name": "part", "count": 1}]),
        event("craft_grid", consumed=[{"name": "part", "count": 1}]),
    ]
    rows = consequence_actions(
        events, stale_by_event={1: ["cf_old"]}, resource_nodes={},
        predicted_products={1: {"part"}},
    )
    assert rows[0]["consumer_event_indices"] == [3]
    assert rows[0]["consequence_commit"] == 1


def test_terminal_submission_and_replan_are_distinct():
    committed = [
        event("mine", success=False, params={"nodeId": "ore_1"}),
        event("submit", params={"result": {"feasible": False}}),
    ]
    probed = [
        event("mine", success=False, params={"nodeId": "ore_1"}),
        event("check_name", params={"name": "replacement"}),
        event("submit", params={"result": {"feasible": True}}),
    ]
    left = consequence_actions(
        committed, stale_by_event={0: ["cf_old"]}, resource_nodes={"ore_1": "ore"},
    )
    right = consequence_actions(
        probed, stale_by_event={0: ["cf_old"]}, resource_nodes={"ore_1": "ore"},
    )
    assert left[0]["witnesses"] == ["terminal_submission"]
    assert right[0]["witnesses"] == []
