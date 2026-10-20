"""Consequence-based HTR classification over immutable schema-v2 logs."""
from __future__ import annotations

import json
from collections import defaultdict, deque
from typing import Any, Iterable

from .external_scoring import ACTION_METHODS


def _item(value: Any) -> Any:
    return value.removeprefix("minecraft:") if isinstance(value, str) else value


def action_signature(event: dict[str, Any], resource_nodes: dict[str, str]) -> str:
    params = dict(event.get("params", {}))
    if event.get("method") == "mine":
        node = str(params.pop("nodeId", ""))
        params["node_type"] = _item(resource_nodes.get(node, node))
    return json.dumps(
        [event.get("method"), params], sort_keys=True, separators=(",", ":"),
    )


def _positive_flows(rows: Iterable[dict[str, Any]]) -> Iterable[tuple[str, int]]:
    for row in rows:
        name, count = _item(row.get("name")), row.get("count")
        if isinstance(name, str) and isinstance(count, (int, float)) and count > 0:
            yield name, int(count)


def inventory_dependencies(
    events: list[dict[str, Any]],
) -> dict[int, list[tuple[int, str, int]]]:
    """Map a producing event to later semantic consumers using FIFO lots."""
    stock: dict[str, deque[tuple[int, int | None]]] = defaultdict(deque)
    for event in events:
        if event.get("method") == "inventory" and isinstance(event.get("result"), list):
            for name, count in _positive_flows(event["result"]):
                stock[name].append((count, None))
            break
    dependencies: dict[int, list[tuple[int, str, int]]] = defaultdict(list)
    for index, event in enumerate(events):
        flow = event.get("item_flow", {})
        for name, needed in _positive_flows(flow.get("consumed", [])):
            queue = stock[name]
            while needed > 0 and queue:
                amount, producer = queue.popleft()
                taken = min(amount, needed)
                if producer is not None and event.get("method") in ACTION_METHODS:
                    dependencies[producer].append((index, name, taken))
                needed -= taken
                if amount > taken:
                    queue.appendleft((amount - taken, producer))
        for name, count in _positive_flows(flow.get("produced", [])):
            stock[name].append((count, index))
    return dependencies


def consequence_actions(
    events: list[dict[str, Any]], *, stale_by_event: dict[int, list[str]],
    resource_nodes: dict[str, str], predicted_products: dict[int, set[str]] | None = None,
    mismatches: dict[int, bool] | None = None,
) -> list[dict[str, Any]]:
    predicted_products = predicted_products or {}
    mismatches = mismatches or {}
    dependencies = inventory_dependencies(events)
    semantic_indices = [
        index for index, event in enumerate(events)
        if event.get("method") in ACTION_METHODS
    ]
    signatures = {
        index: action_signature(events[index], resource_nodes)
        for index in semantic_indices
    }
    output = []
    for semantic_index, event_index in enumerate(semantic_indices):
        stale_ids = sorted(set(stale_by_event.get(event_index, [])))
        if not stale_ids:
            continue
        event = events[event_index]
        products = {
            name for name, _ in _positive_flows(
                event.get("item_flow", {}).get("produced", []),
            )
        }
        expected = {_item(value) for value in predicted_products.get(event_index, set())}
        eligible = products & expected if expected else products
        consumed = [
            (consumer, name, count)
            for consumer, name, count in dependencies.get(event_index, [])
            if name in eligible
        ]
        mismatch = bool(mismatches.get(event_index, event.get("success") is False))
        repeated_at = next((
            later for later in semantic_indices[semantic_index + 1:]
            if mismatch and signatures[later] == signatures[event_index]
        ), None)
        later_semantic = semantic_indices[semantic_index + 1:]
        submit_at = next((
            index for index in range(event_index + 1, len(events))
            if events[index].get("method") == "submit"
        ), None)
        terminal_submit = submit_at is not None and not later_semantic
        witnesses = []
        if consumed:
            witnesses.append("consumed_predicted_product")
        if repeated_at is not None:
            witnesses.append("repeated_after_mismatch")
        if terminal_submit:
            witnesses.append("terminal_submission")
        output.append({
            "event_index": event_index,
            "semantic_index": semantic_index,
            "method": event.get("method"),
            "signature": signatures[event_index],
            "stale_mechanism_ids": stale_ids,
            "mismatch": int(mismatch),
            "predicted_products": sorted(expected),
            "consumed_products": sorted({name for _, name, _ in consumed}),
            "consumer_event_indices": sorted({index for index, _, _ in consumed}),
            "repeated_at": "" if repeated_at is None else repeated_at,
            "terminal_submit_at": "" if not terminal_submit else submit_at,
            "consequence_commit": int(bool(witnesses)),
            "witnesses": witnesses,
        })
    return output


def episode_htr(actions: list[dict[str, Any]]) -> tuple[int, int]:
    return int(bool(actions)), int(any(row["consequence_commit"] for row in actions))
