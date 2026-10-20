"""Zero-call A7 v3-d replay over immutable diagnostic action logs."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from arbiter.evaluation.consequence_htr import inventory_dependencies
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import (
    _inventory_before, classify_actions_v3, event_inputs, intrinsic_discriminative,
    prerequisite_ready,
)
from scripts.run_measurement_v3_20261014 import external_stale_map


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
BASE = ROOT / "artifacts/post_review/diagnostic_controls_20261016"
OUT = ROOT / "reports/main/post_review/diagnostic_controls_20261016"


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing empty A7 export: {path.name}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def records() -> list[dict[str, Any]]:
    indexed = {}
    for root in (BASE / "run_supervised", BASE / "recovery_v2", BASE / "S"):
        for path in root.glob("**/episodes.jsonl"):
            for row in jsonl(path):
                key = (row["method"], row["condition"], row["task_id"],
                       row["world"], row["source_condition"])
                indexed[key] = row
    if len(indexed) != 160:
        raise RuntimeError(f"A7 expected 160 unique episodes, got {len(indexed)}")
    return list(indexed.values())


def inputs_absent(events: list[dict[str, Any]], index: int, predicted: set[str]) -> bool:
    inventory = _inventory_before(events, index)
    if inventory is None:
        return False
    inputs = event_inputs(events[index]) & predicted
    return bool(inputs) and any(inventory[name] <= 0 for name in inputs)


def main() -> None:
    conflicts = {row["id"]: row for row in load_conflicts(WEBCRAFT / "data/conflicts.jsonl")}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    tasks = {row["id"]: row for row in jsonl(WEBCRAFT / "data/tasks.jsonl")}
    action_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    for record in records():
        events = jsonl(ROOT / record["action_log"])
        stale_map = external_stale_map(events, record["world"], conflicts, nodes)
        actions = classify_actions_v3(
            events, stale_by_event=stale_map, conflicts=conflicts,
            resource_nodes=nodes, target=tasks[record["task_id"]]["target"],
            episode_success=bool(record["success"]),
        )
        dependencies = inventory_dependencies(events)
        for row in actions:
            index = int(row["event_index"])
            event = events[index]
            intrinsic = intrinsic_discriminative(
                event, row["stale_mechanism_ids"], world=record["world"],
                conflicts=conflicts, resource_nodes=nodes,
            )
            discriminative = int(bool(intrinsic or row["referenced_at"]))
            predicted = set(row["predicted_products"])
            references = [int(value) for value in row["referenced_at"]]
            repeats = [int(value) for value in row["repeated_at"]]
            fifo = any(consumer > index and name in predicted
                       for consumer, name, _ in dependencies.get(index, []))
            absent = any(inputs_absent(events, ref, predicted) for ref in references)
            denied = int(bool(row["mismatch"]))
            commit = row["v3_label"] == "commit"
            rule_i = int(discriminative and denied and commit and (fifo or absent))
            rule_ii = int(discriminative and denied and commit and bool(repeats))
            rule_iii = int(discriminative and denied and commit and row["affirmative_submit_at"] != "")
            action_rows.append({
                "method": record["method"], "condition": record["condition"],
                "task_id": record["task_id"], "world": record["world"],
                "source_condition": record["source_condition"], "event_index": index,
                "action_method": event.get("method"), "v3_label": row["v3_label"],
                "mismatch": denied, "discriminative": discriminative,
                "prerequisite_ready": prerequisite_ready(events, index),
                "v3d_rule_i": rule_i, "v3d_rule_ii": rule_ii,
                "v3d_rule_iii_prime": rule_iii,
                "v3d_propagation": int(bool(rule_i or rule_ii or rule_iii)),
                "undetermined": int(row["v3_label"] == "undetermined"),
            })
        current = [row for row in action_rows
                   if row["method"] == record["method"] and row["condition"] == record["condition"]
                   and row["task_id"] == record["task_id"] and row["world"] == record["world"]
                   and row["source_condition"] == record["source_condition"]]
        relevant = [row for row in current if row["mismatch"] and row["discriminative"]]
        lower = int(any(row["v3d_propagation"] for row in relevant))
        upper = int(lower or any(row["undetermined"] for row in relevant))
        episode_rows.append({
            "method": record["method"], "condition": record["condition"],
            "task_id": record["task_id"], "world": record["world"],
            "source_condition": record["source_condition"],
            "denied_discriminative_actions": len(relevant),
            "v3d_episode_lower": lower, "v3d_episode_upper": upper,
        })
    summary = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in episode_rows:
        grouped[(row["method"], row["condition"])].append(row)
    for (method, condition), values in sorted(grouped.items()):
        summary.append({
            "method": method, "condition": condition, "n": len(values),
            "v3d_episode_lower": sum(row["v3d_episode_lower"] for row in values) / len(values),
            "v3d_episode_upper": sum(row["v3d_episode_upper"] for row in values) / len(values),
            "episodes_with_denied_discriminative_action": sum(bool(row["denied_discriminative_actions"]) for row in values),
        })
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "v3d_actions.csv", action_rows)
    write_csv(OUT / "v3d_episodes.csv", episode_rows)
    write_csv(OUT / "v3d_summary.csv", summary)
    print(json.dumps({"actions": len(action_rows), "episodes": len(episode_rows), "summary": summary}, sort_keys=True))


if __name__ == "__main__":
    main()
