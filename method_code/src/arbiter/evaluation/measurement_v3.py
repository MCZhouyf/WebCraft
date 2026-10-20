"""Deterministic post-review measurement-v3 scoring helpers."""
from __future__ import annotations

import json
from collections import Counter
from typing import Any, Iterable

from .consequence_htr import action_signature
from .external_scoring import ACTION_METHODS, _matches


def item(value: Any) -> Any:
    return value.removeprefix("minecraft:") if isinstance(value, str) else value


def family(assertion: dict[str, Any]) -> str:
    kind = assertion.get("kind")
    if kind == "drop":
        return "drop"
    if kind == "name":
        return "name"
    if kind == "recipe" and assertion.get("station") in {"furnace", "blasting"}:
        return "smelting"
    if kind in {"recipe", "concrete_grid"}:
        return "recipe"
    return "other"


def subject(assertion: dict[str, Any]) -> str:
    kind = assertion.get("kind")
    if kind == "drop":
        return str(item(assertion.get("block", "")))
    if kind == "name":
        return str(item(assertion.get("item", "")))
    return str(item(assertion.get("output", assertion.get("item", ""))))


def predicted_products(assertion: dict[str, Any]) -> set[str]:
    if assertion.get("kind") == "drop":
        return {str(item(value)) for value in assertion.get("drops", []) if value}
    output = assertion.get("output")
    return {str(item(output))} if output else set()


def positive_flow(rows: Iterable[dict[str, Any]]) -> list[str]:
    return [
        str(item(row.get("name")))
        for row in rows
        if row.get("name") and isinstance(row.get("count"), (int, float)) and row["count"] > 0
    ]


def event_inputs(event: dict[str, Any]) -> set[str]:
    params = event.get("params", {})
    values: list[Any] = []
    if event.get("method") == "craft_grid":
        values.extend(params.get("layout") or [])
    elif event.get("method") == "smelt":
        values.extend([params.get("input"), params.get("fuel")])
    elif event.get("method") == "check_name":
        values.append(params.get("name"))
    values.extend(row.get("name") for row in event.get("item_flow", {}).get("consumed", []))
    return {str(item(value)) for value in values if value}


def actual_products(event: dict[str, Any]) -> set[str]:
    values = set(positive_flow(event.get("item_flow", {}).get("produced", [])))
    result = event.get("result", {})
    if isinstance(result, dict):
        for key in ("product", "output", "drop"):
            value = result.get(key)
            if isinstance(value, str):
                values.add(str(item(value)))
        for value in result.get("drops", []) if isinstance(result.get("drops"), list) else []:
            if isinstance(value, str):
                values.add(str(item(value)))
            elif isinstance(value, dict) and value.get("name"):
                values.add(str(item(value["name"])))
    return values


def actual_product_counts(event: dict[str, Any]) -> Counter[str]:
    counts: Counter[str] = Counter()
    for row in event.get("item_flow", {}).get("produced", []):
        if row.get("name") and isinstance(row.get("count"), (int, float)):
            counts[str(item(row["name"]))] += int(row["count"])
    return counts


def result_mismatch(event: dict[str, Any], assertions: list[dict[str, Any]]) -> bool:
    if event.get("success") is False:
        return True
    if event.get("method") == "check_name":
        result = event.get("result", {})
        expected = any(row.get("exists") is True for row in assertions)
        return isinstance(result, dict) and isinstance(result.get("resolvable"), bool) and result["resolvable"] != expected
    expected = set().union(*(predicted_products(row) for row in assertions))
    actual = actual_products(event)
    if expected and not (expected & actual):
        return True
    counts = actual_product_counts(event)
    count_predictions = [
        (str(item(row.get("output"))), int(row["count"]))
        for row in assertions
        if row.get("output") and isinstance(row.get("count"), (int, float))
    ]
    return bool(count_predictions and not any(counts[name] == count for name, count in count_predictions))


def affirmative_submit(event: dict[str, Any]) -> bool:
    if event.get("method") != "submit":
        return False
    result = event.get("params", {}).get("result", {})
    if not isinstance(result, dict):
        return False
    return result.get("feasible") is True or result.get("completed") is True or result.get("attained") is True


def infeasible_submit(event: dict[str, Any]) -> bool:
    return event.get("method") == "submit" and event.get("params", {}).get("result", {}).get("feasible") is False


def classify_actions_v3(
    events: list[dict[str, Any]], *, stale_by_event: dict[int, list[str]],
    conflicts: dict[str, dict[str, Any]], resource_nodes: dict[str, str], target: str,
    episode_success: bool = False,
) -> list[dict[str, Any]]:
    """Classify each stale semantic action under the preregistered v3 rule."""
    semantic = [i for i, event in enumerate(events) if event.get("method") in ACTION_METHODS]
    signatures = {i: action_signature(events[i], resource_nodes) for i in semantic}
    output: list[dict[str, Any]] = []
    for semantic_index, event_index in enumerate(semantic):
        stale_ids = sorted(set(stale_by_event.get(event_index, [])))
        if not stale_ids:
            continue
        event = events[event_index]
        stale_assertions = [conflicts[value]["assertion"] for value in stale_ids]
        predicted = set().union(*(predicted_products(row) for row in stale_assertions))
        later = semantic[semantic_index + 1:]
        referenced = [i for i in later if event_inputs(events[i]) & predicted]
        mismatch = result_mismatch(event, stale_assertions)
        repeated = [
            i for i in later
            if mismatch and signatures[i] == signatures[event_index]
            and set(stale_by_event.get(i, [])) & set(stale_ids)
        ]
        submit_indices = [i for i in range(event_index + 1, len(events)) if events[i].get("method") == "submit"]
        affirmative = next((i for i in submit_indices if affirmative_submit(events[i])), None)
        infeasible = next((i for i in submit_indices if infeasible_submit(events[i])), None)
        route_changes = [
            i for i in later
            if signatures[i] != signatures[event_index]
            or not (set(stale_by_event.get(i, [])) & set(stale_ids))
        ]
        affirmative_no_change = affirmative is not None and not any(i < affirmative for i in route_changes)
        progress = []
        for i in route_changes:
            produced = actual_products(events[i])
            later_inputs = set().union(*(event_inputs(events[j]) for j in semantic if j > i))
            if events[i].get("success") is True and (
                str(item(target)) in produced or bool(produced & later_inputs) or episode_success
            ):
                progress.append(i)
        witnesses: list[str] = []
        if referenced:
            witnesses.append("predicted_product_referenced")
        if repeated:
            witnesses.append("exact_stale_action_repeated")
        if mismatch and affirmative_no_change:
            witnesses.append("affirmative_submit_without_route_change")
        verify_witnesses: list[str] = []
        if mismatch and not referenced and progress:
            verify_witnesses.append("different_goal_progress_action")
        if mismatch and not referenced and infeasible is not None:
            verify_witnesses.append("infeasible_submission")
        if witnesses:
            label = "commit"
        elif verify_witnesses:
            label = "verify"
        else:
            label = "undetermined"
        output.append({
            "event_index": event_index,
            "semantic_index": semantic_index,
            "method": event.get("method"),
            "signature": signatures[event_index],
            "stale_mechanism_ids": stale_ids,
            "mechanism_families": sorted({family(row) for row in stale_assertions}),
            "predicted_products": sorted(predicted),
            "mismatch": int(mismatch),
            "referenced_at": referenced,
            "repeated_at": repeated,
            "affirmative_submit_at": "" if affirmative is None else affirmative,
            "infeasible_submit_at": "" if infeasible is None else infeasible,
            "route_change_at": route_changes,
            "v3_label": label,
            "commit_witnesses": witnesses,
            "verify_witnesses": verify_witnesses,
        })
    return output


def episode_bounds(rows: list[dict[str, Any]]) -> tuple[int, int]:
    labels = {row["v3_label"] for row in rows}
    return int("commit" in labels), int(bool(labels & {"commit", "undetermined"}))


def intrinsic_discriminative(
    event: dict[str, Any], stale_ids: list[str], *, world: str,
    conflicts: dict[str, dict[str, Any]], resource_nodes: dict[str, str],
) -> bool:
    """Whether no target-world true assertion licenses the same public action."""
    for stale_id in stale_ids:
        stale = conflicts[stale_id]["assertion"]
        stale_family, stale_subject = family(stale), subject(stale)
        alternatives = [
            row["assertion"] for row in conflicts.values()
            if row["truth_vector"].get(world) is True
            and family(row["assertion"]) == stale_family
            and subject(row["assertion"]) == stale_subject
        ]
        if not any(_matches(event, assertion, resource_nodes) for assertion in alternatives):
            return True
    return False


def _inventory_before(events: list[dict[str, Any]], event_index: int) -> Counter[str] | None:
    snapshot_index = next((
        index for index in range(event_index - 1, -1, -1)
        if events[index].get("method") == "inventory" and isinstance(events[index].get("result"), list)
    ), None)
    if snapshot_index is None:
        return None
    inventory: Counter[str] = Counter()
    for row in events[snapshot_index]["result"]:
        if row.get("name") and isinstance(row.get("count"), (int, float)):
            inventory[str(item(row["name"]))] += int(row["count"])
    for event in events[snapshot_index + 1:event_index]:
        for row in event.get("item_flow", {}).get("consumed", []):
            if row.get("name") and isinstance(row.get("count"), (int, float)):
                inventory[str(item(row["name"]))] -= int(row["count"])
        for row in event.get("item_flow", {}).get("produced", []):
            if row.get("name") and isinstance(row.get("count"), (int, float)):
                inventory[str(item(row["name"]))] += int(row["count"])
    return inventory


def prerequisite_ready(events: list[dict[str, Any]], event_index: int) -> str:
    """Conservative public-log-only readiness annotation."""
    event = events[event_index]
    result = event.get("result", {})
    text = json.dumps(result, sort_keys=True).lower()
    inventory = _inventory_before(events, event_index)
    if event.get("success") is True:
        return "true"
    if inventory is None:
        return "unknown"
    params = event.get("params", {})
    required: Counter[str] = Counter()
    if event.get("method") == "craft_grid":
        required.update(str(item(value)) for value in params.get("layout", []) if value)
    elif event.get("method") == "smelt":
        if params.get("input"):
            required[str(item(params["input"]))] += 1
        if params.get("fuel"):
            required[str(item(params["fuel"]))] += 1
    ingredients_missing = any(inventory[name] < count for name, count in required.items())
    has_pickaxe = any(count > 0 and name.endswith("_pickaxe") for name, count in inventory.items())
    if any(value in text for value in ("missing ingredient", "missing input")) and ingredients_missing:
        return "false"
    if any(value in text for value in ("insufficient fuel", "missing fuel")):
        return "false" if not params.get("fuel") or inventory[str(item(params["fuel"]))] <= 0 else "unknown"
    if "missing tool" in text and not has_pickaxe:
        return "false"
    if "tool too weak" in text and has_pickaxe:
        return "false"
    if not ingredients_missing and any(value in text for value in ("invalid recipe", "no matching recipe", "rejected")):
        return "true"
    return "unknown"


def confusion_counts(rows: Iterable[dict[str, Any]]) -> Counter[tuple[str, int]]:
    return Counter((str(row["v3_label"]), int(row["plan_source_commit"])) for row in rows)
