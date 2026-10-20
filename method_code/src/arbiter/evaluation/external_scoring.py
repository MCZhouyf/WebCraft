"""Evaluator-only scoring for post-review external-agent traces."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Iterable


ACTION_METHODS = frozenset({"mine", "craft_grid", "smelt", "check_name"})


def load_conflicts(path: Path) -> tuple[dict[str, Any], ...]:
    """Load frozen evaluator conflicts without exposing them to an agent."""
    rows = tuple(json.loads(line) for line in path.read_text().splitlines() if line.strip())
    required = {"id", "assertion", "truth_vector"}
    if any(not isinstance(row, dict) or not required <= set(row) for row in rows):
        raise ValueError("invalid evaluator conflict row")
    return rows


def load_resource_nodes(path: Path) -> dict[str, str]:
    raw = json.loads(path.read_text())
    rows = raw.get("resources") if isinstance(raw, dict) else None
    if not isinstance(rows, list):
        raise ValueError("invalid arena resource table")
    return {str(row["nodeId"]): str(row["block"]) for row in rows}


def _item(value: Any) -> Any:
    return value.removeprefix("minecraft:") if isinstance(value, str) else value


def _trim_layout(layout: Iterable[Any]) -> tuple[tuple[str | None, ...], ...]:
    cells = [_item(value) for value in layout]
    if len(cells) != 9 or any(value is not None and not isinstance(value, str) for value in cells):
        return tuple()
    rows = [cells[index:index + 3] for index in range(0, 9, 3)]
    while rows and all(cell is None for cell in rows[0]):
        rows.pop(0)
    while rows and all(cell is None for cell in rows[-1]):
        rows.pop()
    if not rows:
        return tuple()
    left = min(index for row in rows for index, cell in enumerate(row) if cell is not None)
    right = max(index for row in rows for index, cell in enumerate(row) if cell is not None)
    return tuple(tuple(row[left:right + 1]) for row in rows)


def _grid_matches(layout: list[Any], assertion: dict[str, Any]) -> bool:
    if assertion.get("kind") == "concrete_grid":
        return [_item(value) for value in layout] == [_item(value) for value in assertion.get("layout", [])]
    if assertion.get("kind") != "recipe" or assertion.get("station") != "crafting_grid":
        return False
    actual = _trim_layout(layout)
    grid = assertion.get("grid")
    if bool(assertion.get("ordered")):
        if not isinstance(grid, list):
            return False
        expected = tuple(tuple(cell for cell in row) for row in grid)
        variants = (expected, tuple(tuple(reversed(row)) for row in expected))
        for variant in variants:
            if len(actual) != len(variant) or any(len(left) != len(right) for left, right in zip(actual, variant)):
                continue
            if all(
                current is None if options is None else current in {_item(value) for value in options}
                for current_row, expected_row in zip(actual, variant)
                for current, options in zip(current_row, expected_row)
            ):
                return True
        return False
    ingredients = assertion.get("ingredients")
    if not isinstance(ingredients, list):
        return False
    remaining = [tuple(_item(value) for value in options) for options in ingredients]
    cells = sorted(value for row in actual for value in row if value is not None)
    for value in cells:
        index = next((i for i, options in enumerate(remaining) if value in options), None)
        if index is None:
            return False
        remaining.pop(index)
    return not remaining


def _matches(event: dict[str, Any], assertion: dict[str, Any], nodes: dict[str, str]) -> bool:
    method, params = event.get("method"), event.get("params", {})
    kind = assertion.get("kind")
    if method == "check_name" and kind == "name":
        return _item(params.get("name")) == _item(assertion.get("item"))
    if method == "mine" and kind == "drop":
        return _item(nodes.get(str(params.get("nodeId")))) == _item(assertion.get("block"))
    if method == "craft_grid":
        layout = params.get("layout")
        return isinstance(layout, list) and _grid_matches(layout, assertion)
    if method == "smelt" and kind == "recipe" and assertion.get("station") == "furnace":
        ingredients = assertion.get("ingredients")
        return bool(
            isinstance(ingredients, list) and ingredients
            and _item(params.get("input")) in {_item(value) for value in ingredients[0]}
        )
    return False


def score_external_trace(
    events: list[dict[str, Any]], *, world: str, conflicts: tuple[dict[str, Any], ...],
    resource_nodes: dict[str, str], true_feasible: bool, reported_feasible: bool | None,
) -> dict[str, Any]:
    """Score a finished trace; this function must never run inside the agent loop."""
    stale_all: list[int] = []
    stale_commit: list[int] = []
    self_reported_stale_commit: list[int] = []
    matched: list[dict[str, Any]] = []
    depended: set[str] = set()
    disagreements = 0
    for index, event in enumerate(events):
        if event.get("method") not in ACTION_METHODS:
            continue
        candidates = [row for row in conflicts if _matches(event, row["assertion"], resource_nodes)]
        stale = [
            row for row in candidates
            if row["truth_vector"].get(world) is False
            and any(value is True for value in row["truth_vector"].values())
        ]
        stale_ids = [str(row["id"]) for row in stale]
        all_ids = [str(row["id"]) for row in candidates]
        # Keep the post-review A4 rule identical to the deterministic replay:
        # the first adjudication of a DV claim is behavioral probing, regardless
        # of the model-authored intent label.
        behavioral_probe = bool(all_ids and not any(value in depended for value in all_ids))
        declared_probe = event.get("intent") == "probe"
        disagreements += int(behavioral_probe != declared_probe)
        if stale_ids:
            stale_all.append(index)
            if not behavioral_probe:
                stale_commit.append(index)
            if not declared_probe:
                self_reported_stale_commit.append(index)
        if not behavioral_probe:
            depended.update(all_ids)
        if all_ids:
            matched.append({
                "event_index": index, "mechanism_ids": all_ids,
                "stale_mechanism_ids": stale_ids,
                "behavioral_probe": behavioral_probe,
                "declared_probe": declared_probe,
            })
    probe_events = [event for event in events if event.get("intent") == "probe"]
    execution_failed = any(
        event.get("method") in ACTION_METHODS and event.get("success") is False
        for event in events
    )
    return {
        "HTR_all": int(bool(stale_all)),
        "HTR_commit": int(bool(stale_commit)),
        "self_reported_HTR_commit": int(bool(self_reported_stale_commit)),
        "stale_event_indices": stale_all,
        "stale_commit_event_indices": stale_commit,
        "self_reported_stale_commit_event_indices": self_reported_stale_commit,
        "probe_count": len(probe_events),
        "probe_cost": sum(float(event["cost"]["primary"]) for event in probe_events),
        "intent_label_disagreements": disagreements,
        "false_abstention": bool(true_feasible and reported_feasible is False),
        "execution_failure": bool(execution_failed),
        "search_count": sum(event.get("method") == "search" for event in events),
        "matched_DV_actions": matched,
    }
