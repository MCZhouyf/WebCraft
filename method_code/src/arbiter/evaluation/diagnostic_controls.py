"""A7 diagnostic-control evidence projections.

These helpers are deliberately separate from the frozen evaluation path.  They
may read the WebCraft truth cache only to construct the author-authorized,
privileged diagnostic conditions, then expose the agent only ordinary public
search rows and an initial observation.
"""
from __future__ import annotations

import hashlib
import json
import random
from collections import Counter
from pathlib import Path
from typing import Any

from .external_controls import OracleSearchAdapter, oracle_search_rows


DIAGNOSTIC_INSTANCES = (
    ("eff_002", "1.16.5", "matched_1.16.5"), ("eff_005", "1.16.5", "matched_1.16.5"),
    ("eff_004", "1.16.5", "mismatched_new2old"), ("eff_008", "1.16.5", "mismatched_new2old"),
    ("eff_005", "1.19", "matched_1.19"), ("eff_008", "1.19", "matched_1.19"),
    ("eff_005", "1.21.11", "matched_1.21.11"), ("eff_008", "1.21.11", "matched_1.21.11"),
    ("eff_001", "1.21.11", "mismatched_old2new"), ("eff_003", "1.21.11", "mismatched_old2new"),
    ("fea_003", "1.16.5", "matched_1.16.5"), ("fea_007", "1.16.5", "matched_1.16.5"),
    ("fea_003", "1.16.5", "mismatched_new2old"), ("fea_007", "1.16.5", "mismatched_new2old"),
    ("fea_003", "1.19", "matched_1.19"), ("fea_008", "1.21.11", "matched_1.21.11"),
    ("fea_010", "1.21.11", "matched_1.21.11"), ("fea_004", "1.21.11", "mismatched_old2new"),
    ("fea_006", "1.21.11", "mismatched_old2new"), ("pla_012", "1.16.5", "matched_1.16.5"),
    ("pla_004", "1.16.5", "mismatched_new2old"), ("pla_010", "1.19", "matched_1.19"),
    ("pla_012", "1.21.11", "matched_1.21.11"), ("pla_007", "1.21.11", "mismatched_old2new"),
    ("eff_006", "1.16.5", "matched_1.16.5"), ("eff_006", "1.16.5", "mismatched_new2old"),
    ("eff_006", "1.19", "matched_1.19"), ("fea_006", "1.16.5", "matched_1.16.5"),
    ("fea_001", "1.16.5", "mismatched_new2old"), ("fea_008", "1.19", "matched_1.19"),
    ("fea_007", "1.21.11", "matched_1.21.11"), ("fea_007", "1.21.11", "mismatched_old2new"),
)


def _public_row(text: str, *, key: str, timestamp: str = "2024-01-01T00:00:00Z") -> dict[str, Any]:
    digest = hashlib.sha256(key.encode()).hexdigest()
    return {
        "text": text, "source_site": "minecraft_wiki", "page_title": "Mechanism fact",
        "revid": int(digest[:8], 16), "timestamp": timestamp,
        "chunk_id": "diagnostic_" + digest[:20],
    }


def _conflict_rows(task: dict[str, Any], world: str, conflicts: Any) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    conflict_map = {str(row["id"]): row for row in conflicts}
    for conflict_id in task.get("conflict_ids", []):
        conflict = conflict_map.get(conflict_id)
        if not conflict:
            continue
        assertion = conflict.get("assertion", {})
        truth = conflict.get("truth_vector", {})
        if truth.get(world) is True:
            continue
        kind = str(assertion.get("kind", "mechanism"))
        subject = str(assertion.get("block") or assertion.get("item") or "item").replace("_", " ")
        payload = ", ".join(str(value).replace("_", " ") for value in assertion.get("drops", []))
        text = f"Mechanism fact | item: {subject} | mechanism: {kind} | result: {payload or 'available'} | source timestamp: 2021-02-10T07:44:16Z."
        rows.append(_public_row(text, key=f"conflict:{task['id']}:{world}:{conflict_id}"))
    return rows


def evidence_rows(task: dict[str, Any], world: str, condition: str, *, webcraft_root: Path,
                  requirements_from_task: Any, conflicts: Any) -> list[dict[str, Any]]:
    """Construct the A7 K/K+C/K+N public evidence package deterministically."""
    correct = oracle_search_rows(task, world, webcraft_root=webcraft_root,
                                 requirements_from_task=requirements_from_task)
    if condition == "K":
        rows = correct
    elif condition == "K+C":
        rows = correct + _conflict_rows(task, world, conflicts)
    elif condition == "K+N":
        neutrals = []
        for index, row in enumerate(correct):
            # Equal cardinality and near-matched public text length; these facts are task-unrelated.
            text = str(row["text"])
            neutral = ("Mechanism fact | item: decorative stone | mechanism: name | result: decorative stone "
                       + ("x" * max(0, len(text) - 88)))
            neutrals.append(_public_row(neutral, key=f"neutral:{task['id']}:{world}:{index}", timestamp=str(row["timestamp"])))
        rows = correct + neutrals
    else:
        raise ValueError(f"unknown diagnostic condition {condition}")
    rng = random.Random(f"20261016:{task['id']}:{world}:{condition}")
    rows = [dict(row) for row in rows]
    rng.shuffle(rows)
    return rows


class DiagnosticFactsAdapter(OracleSearchAdapter):
    """Search replacement plus one initial public observation containing facts."""

    def __init__(self, base: Any, rows: list[dict[str, Any]]) -> None:
        super().__init__(base, rows)
        self._initial = True

    def inventory(self, *, intent: str | None = None) -> Any:
        inventory = self.base.inventory(intent=intent)
        if self._initial:
            self._initial = False
            return {"inventory": inventory, "diagnostic_mechanism_facts": self.rows}
        return inventory


def s_initial_inventory(task: dict[str, Any], world: str, conflicts: Any) -> tuple[Counter[str], dict[str, Any]]:
    """Replay the evaluator-only oracle prefix before its first conflict action.

    The resulting counter is passed only to the simulator constructor.  Neither
    the oracle action sequence nor the cutoff metadata is placed in agent input.
    """
    initial = Counter({str(row["name"]): int(row["count"])
                       for row in task["standard_items"] + task.get("task_supplements", [])})
    plan = (task.get("oracle_plans") or {}).get(world)
    if not isinstance(plan, dict):
        return initial, {"applicable": False, "reason": "no_oracle_plan"}
    conflict_map = {str(row["id"]): row for row in conflicts}
    relevant = [conflict_map[item] for item in task.get("conflict_ids", []) if item in conflict_map]

    def is_conflict_action(action: dict[str, Any]) -> bool:
        values = {str(action.get("node_type", "")), str(action.get("input", "")),
                  str((action.get("expected") or {}).get("name", "")), str(action.get("recipe_id", ""))}
        values.update(str(item) for item in action.get("layout", []) if item is not None)
        for conflict in relevant:
            assertion = conflict.get("assertion", {})
            items = set(str(item) for item in conflict.get("involved_items", []))
            items.update(str(item) for item in assertion.get("drops", []))
            items.update(str(assertion.get(key, "")) for key in ("block", "item", "output"))
            if values & (items - {""}):
                return True
        return False

    def replay(action: dict[str, Any]) -> None:
        method = action.get("method")
        expected = action.get("expected") or {}
        if method == "craft_grid":
            for item in action.get("layout", []):
                if item is not None:
                    initial[str(item)] -= 1
        elif method == "smelt":
            count = int(action.get("n", 1))
            initial[str(action["input"])] -= count
            initial[str(action.get("fuel", "coal"))] -= int(action.get("fuel_count", 1))
        name = expected.get("name")
        if name:
            initial[str(name)] += int(expected.get("count", 1))
        for item in list(initial):
            if initial[item] <= 0:
                del initial[item]

    actions = list(plan.get("actions") or [])
    for index, action in enumerate(actions):
        if is_conflict_action(action):
            return initial, {"applicable": True, "cutoff_action_index": index}
        replay(action)
    return initial, {"applicable": False, "reason": "no_conflict_action_in_plan"}
