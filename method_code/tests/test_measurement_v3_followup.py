from scripts.run_measurement_v3_followup_20261015 import (
    action_dependency, action_parts, first_difference, legal_format,
)


def event(method, *, params=None, success=True, produced=None, consumed=None, intent=None):
    row = {
        "method": method, "params": params or {}, "success": success,
        "result": {"success": success, "cost": {"primary": 99}},
        "cost": {"primary": 1},
        "item_flow": {
            "produced": [{"name": name, "count": 1} for name in produced or []],
            "consumed": [{"name": name, "count": 1} for name in consumed or []],
        },
    }
    if intent is not None:
        row["intent"] = intent
    return row


def test_action_identity_ignores_intent_and_nested_result_cost():
    left = event("check_name", params={"name": "stone"}, intent="probe")
    right = event("check_name", params={"name": "stone"}, intent="commit")
    right["result"]["cost"] = {"primary": 3}
    assert action_parts(left) == action_parts(right)


def test_first_difference_reports_parameter_change():
    left = [action_parts(event("check_name", params={"name": "stone"}))]
    right = [action_parts(event("check_name", params={"name": "granite"}))]
    position, kind, _, _ = first_difference(left, right)
    assert position == 0
    assert kind == "parameter_different"


def test_same_named_initial_inventory_is_not_causal_lot_dependency():
    inventory = event("inventory")
    inventory["result"] = [{"name": "stone", "count": 1}]
    stale = event("craft_grid", params={"layout": [None] * 9}, success=False)
    later = event("craft_grid", params={"layout": ["stone"] + [None] * 8}, consumed=["stone"])
    name_ref, causal, source, _ = action_dependency([inventory, stale, later], 1, {"stone"})
    assert name_ref == 1
    assert causal == 0
    assert source == "same_name_other_source"


def test_interface_format_validation():
    assert legal_format(event("mine", params={"nodeId": "iron_1"}))
    assert legal_format(event("craft_grid", params={"layout": [None] * 9}))
    assert not legal_format(event("craft_grid", params={"layout": [None] * 8}))
    assert not legal_format(event("smelt", params={"input": ""}))
