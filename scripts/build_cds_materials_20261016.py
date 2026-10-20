#!/usr/bin/env python3
"""Build deterministic agent-visible and evaluator-private W7a CDS materials."""
from __future__ import annotations

import hashlib
import json
import os
import random
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARBITER = Path("/root/autodl-tmp/arbiter")
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ARBITER / "src")]

from arbiter.normalization import mechanism_id  # noqa: E402
from conflict_core import recipe_assertion  # noqa: E402
from oracle.planner import VERSIONS, OraclePlanner  # noqa: E402


TASK_PATH = ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl"
FAMILY_PATH = ROOT / "config/ext_v2/mechanism_families.json"
VISIBLE = ROOT / "cds_materials"
PRIVATE = ROOT / "reports/paperA/ext_v2/cds_evaluator"
MANIFEST = ROOT / "cds_materials/cds_manifest.json"
FREEZE = ROOT / "config/ext_v2/freeze_manifest.json"
SEED = 20261016
WORLD_TIMESTAMP = {
    "1.16.5": "2021-02-13T00:00:00Z",
    "1.19": "2022-07-07T00:00:00Z",
    "1.21.11": "2026-01-08T00:00:00Z",
}


def compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def stable_rng(*parts: str) -> random.Random:
    digest = hashlib.sha256((str(SEED) + "|" + "|".join(parts)).encode()).digest()
    return random.Random(int.from_bytes(digest[:8], "big"))


def action_assertion(action: dict[str, Any]) -> dict[str, Any]:
    expected = action.get("expected", {})
    if action["method"] == "mine":
        assertion: dict[str, Any] = {
            "kind": "drop", "block": action["node_type"], "silk_touch": False,
            "drops": [expected["name"]],
        }
        execution = {"output_count": int(expected.get("count", 1))}
        if action.get("requires_tool"):
            execution["requires"] = [action["requires_tool"]]
        assertion["_execution"] = execution
        return assertion
    if action["method"] == "smelt":
        return {
            "kind": "recipe", "output": expected["name"],
            "count": int(expected.get("count", action.get("n", 1))),
            "station": "furnace", "recipe_type": "smelting", "ordered": False,
            "ingredients": [[action["input"]]], "fuel": action["fuel"],
        }
    if action["method"] == "craft_grid":
        cells = list(action["layout"])
        rows = [[([value] if value else []) for value in cells[offset:offset + 3]]
                for offset in range(0, 9, 3)]
        while rows and not any(rows[0]):
            rows.pop(0)
        while rows and not any(rows[-1]):
            rows.pop()
        while rows and not any(row[0] for row in rows):
            rows = [row[1:] for row in rows]
        while rows and not any(row[-1] for row in rows):
            rows = [row[:-1] for row in rows]
        mirror = [list(reversed(row)) for row in rows]
        rows = min((rows, mirror), key=compact)
        return {
            "kind": "recipe", "output": expected["name"],
            "count": int(expected.get("count", 1)), "station": "crafting_grid",
            "recipe_type": "crafting_shaped", "ordered": True, "grid": rows,
        }
    raise ValueError(action["method"])


def public_action_to_assertion(action: dict[str, Any], target: str) -> dict[str, Any]:
    if action["method"] == "mine":
        return {
            "kind": "drop", "block": action["node_type"], "silk_touch": False,
            "drops": [target],
        }
    if action["method"] == "smelt":
        return {
            "kind": "recipe", "output": target, "count": 1,
            "station": "furnace", "recipe_type": "smelting", "ordered": False,
            "ingredients": [[action["input"]]], "fuel": action["fuel"],
        }
    if action["method"] == "craft_grid":
        return action_assertion({
            **action, "expected": {"name": target, "count": 1},
        })
    raise ValueError(action["method"])


def focal_operator_assertion(
    conflict: dict[str, Any], task: dict[str, Any], resource: dict[str, Any]
) -> dict[str, Any] | None:
    assertion = conflict["assertion"]
    if assertion["kind"] == "recipe":
        return assertion
    if assertion["kind"] == "concrete_grid":
        return public_action_to_assertion(
            {"method": "craft_grid", "layout": assertion["layout"]}, assertion["output"]
        )
    subject = assertion.get("item")
    if not subject:
        return None
    # Compile unsupported existence/name assertions through the item's own
    # world-valid production action, independent of the task diagnostic pack.
    initial = Counter({row["name"]: int(row["count"]) for row in resource["standard_inventory"]})
    feasible = []
    for world in VERSIONS:
        if not conflict["truth_vector"][world]:
            continue
        plan = OraclePlanner(
            world, capacities=resource["resource_block_counts"], initial=initial,
            action_limit=40,
        ).plan(subject)
        if plan.feasible:
            feasible.append((plan.primary_cost, len(plan.actions), world, plan))
    if not feasible:
        return None
    plan = min(feasible, key=lambda row: row[:3])[-1]
    if not plan.actions:
        return None
    return action_assertion(plan.actions[-1])


def fact_text(assertion: dict[str, Any], value: bool) -> str:
    kind = assertion["kind"]
    if kind == "drop":
        drops = ", ".join(assertion["drops"])
        return f"{assertion['block']} {'drops' if value else 'does not drop'} {drops}."
    output = assertion["output"]
    if assertion.get("ordered"):
        ingredients = [sorted(cell)[0] for row in assertion.get("grid", []) for cell in row if cell]
    else:
        ingredients = [sorted(slot)[0] for slot in assertion.get("ingredients", []) if slot]
    verb = "can be made" if value else "cannot be made"
    return f"{output} {verb} from {', '.join(ingredients)} at {assertion['station']}."


def neutral_fact_text(assertion: dict[str, Any]) -> str:
    if assertion.get("ordered"):
        ingredients = [sorted(cell)[0] for row in assertion.get("grid", []) for cell in row if cell]
    else:
        ingredients = [sorted(slot)[0] for slot in assertion.get("ingredients", []) if slot]
    return f"{assertion['output']} from {', '.join(dict.fromkeys(ingredients))}."


def neutral_assertions(tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    excluded = {task["target"]["item"] for task in tasks}
    common: set[str] | None = None
    lookup: dict[str, dict[str, Any]] = {}
    for world in VERSIONS:
        truth = json.loads((ROOT / f"truth/{world}.json").read_text())
        current = {}
        for raw in truth["recipes"]:
            assertion = recipe_assertion(raw)
            if (not assertion or assertion["output"] in excluded
                    or assertion.get("station") not in {"crafting_grid", "furnace"}):
                continue
            key = compact(assertion)
            current[key] = assertion
        common = set(current) if common is None else common & set(current)
        lookup.update(current)
    rows = [lookup[key] for key in sorted(common or ())]
    if len(rows) < 20:
        raise RuntimeError("insufficient common neutral facts")
    return rows


def visible_entry(text: str, source: str, timestamp: str) -> dict[str, str]:
    return {"mechanism fact": text, "source": source, "timestamp": timestamp}


def shuffled(rows: list[dict[str, Any]], *parts: str) -> list[dict[str, Any]]:
    result = list(rows)
    stable_rng(*parts).shuffle(result)
    return result


def build_fact(
    conflict_id: str, assertion: dict[str, Any], value: bool,
    source: str, timestamp: str, role: str,
) -> tuple[dict[str, str], dict[str, Any]]:
    text = fact_text(assertion, value)
    visible = visible_entry(text, source, timestamp)
    private = {
        "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
        "conflict_id": conflict_id,
        "mechanism_id": mechanism_id({k: v for k, v in assertion.items() if k != "_execution"}),
        "assertion": assertion, "value": value, "role": role,
        "source": source, "timestamp": timestamp,
    }
    return visible, private


def main() -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit("run with PYTHONHASHSEED=0")
    tasks = jsonl(TASK_PATH)
    task_map = {task["id"]: task for task in tasks}
    family = json.loads(FAMILY_PATH.read_text())
    pairs = family["directed_comparison_pairs"]
    conflicts = {row["id"]: row for row in jsonl(ROOT / "data/conflicts.jsonl")}
    resource = json.loads((ROOT / "config/resource_model.json").read_text())
    neutrals = neutral_assertions(tasks)

    VISIBLE.mkdir(parents=True, exist_ok=True)
    PRIVATE.mkdir(parents=True, exist_ok=True)
    for directory in (VISIBLE, PRIVATE):
        for path in directory.glob("cds_*.json"):
            path.unlink()

    manifest_rows = []
    for pair_index, pair in enumerate(pairs, 1):
        task = task_map[pair["task_id"]]
        target_world = pair["target_world"]
        comparison_world = pair["comparison_world"]
        instance_id = f"cds_{pair_index:04d}_{task['id']}_{target_world.replace('.', '_')}_vs_{comparison_world.replace('.', '_')}"
        focal_private, focal_visible = [], []
        for conflict_id in task["focal_conflict_ids"]:
            conflict = conflicts[conflict_id]
            assertion = focal_operator_assertion(conflict, task, resource)
            if assertion is None:
                continue
            target_value = bool(conflict["truth_vector"][target_world])
            current_v, current_p = build_fact(
                conflict_id, assertion, target_value,
                f"truth_archive_{target_world}", WORLD_TIMESTAMP[target_world], "focal_correct",
            )
            comparison_value = bool(conflict["truth_vector"][comparison_world])
            stale_v, stale_p = build_fact(
                conflict_id, assertion, comparison_value,
                f"historical_archive_{comparison_world}", WORLD_TIMESTAMP[comparison_world], "focal_stale",
            )
            focal_private.append({
                "conflict_id": conflict_id, "assertion": assertion,
                "target_truth": target_value, "comparison_truth": comparison_value,
                "current_visible": current_v, "current_private": current_p,
                "stale_visible": stale_v, "stale_private": stale_p,
            })
            focal_visible.append(visible_entry(
                stale_v["mechanism fact"], stale_v["source"], stale_v["timestamp"]
            ))

        if not focal_private:
            raise RuntimeError(f"no focal candidate compiled for {instance_id}")
        stale = [row for row in focal_private if row["target_truth"] != row["comparison_truth"]]
        if not stale:
            raise RuntimeError(f"comparison pair has no focal truth change: {instance_id}")

        nonfocal_visible, nonfocal_private = [], []
        plan = task["oracle_plans"].get(target_world)
        for index, action in enumerate((plan or {}).get("actions", []), 1):
            assertion = action_assertion(action)
            identity = mechanism_id({k: v for k, v in assertion.items() if k != "_execution"})
            if identity in {row["current_private"]["mechanism_id"] for row in focal_private}:
                continue
            visible, private = build_fact(
                identity, assertion, True, f"truth_source_{index % 3 + 1}",
                WORLD_TIMESTAMP[target_world], "nonfocal_correct",
            )
            if private["mechanism_id"] in {row["mechanism_id"] for row in nonfocal_private}:
                continue
            nonfocal_visible.append(visible)
            nonfocal_private.append(private)

        current_visible = [row["current_visible"] for row in focal_private]
        current_private = [row["current_private"] for row in focal_private]
        stale_visible = [row["stale_visible"] for row in stale]
        stale_private = [row["stale_private"] for row in stale]
        neutral_visible, neutral_private = [], []
        used_neutral = set()
        for stale_index, stale_row in enumerate(stale, 1):
            target_length = len(stale_row["stale_visible"]["mechanism fact"])
            choices = []
            for assertion in neutrals:
                text = neutral_fact_text(assertion)
                identity = mechanism_id(assertion)
                if identity not in used_neutral and len(text) <= target_length:
                    choices.append((target_length - len(text), identity, assertion, text))
            if not choices:
                raise RuntimeError(f"no length-compatible neutral fact for {instance_id}")
            _, identity, assertion, text = min(choices)
            used_neutral.add(identity)
            text = text + " " * (target_length - len(text))
            source = f"neutral_archive_{comparison_world}"
            timestamp = stale_row["stale_visible"]["timestamp"]
            neutral_visible.append(visible_entry(text, source, timestamp))
            neutral_private.append({
                "text_sha256": hashlib.sha256(text.encode()).hexdigest(),
                "conflict_id": identity, "mechanism_id": identity,
                "assertion": assertion, "value": True, "role": "neutral",
                "source": source, "timestamp": timestamp,
            })

        condition_parts = {
            "K": (nonfocal_visible + current_visible, nonfocal_private + current_private),
            "K+S": (nonfocal_visible + current_visible + stale_visible,
                    nonfocal_private + current_private + stale_private),
            "S": (nonfocal_visible + stale_visible, nonfocal_private + stale_private),
            "empty": (nonfocal_visible, nonfocal_private),
            "K+N": (nonfocal_visible + current_visible + neutral_visible,
                    nonfocal_private + current_private + neutral_private),
        }
        visible_conditions, private_conditions = {}, {}
        for condition, (visible_rows, private_rows) in condition_parts.items():
            visible_conditions[condition] = shuffled(visible_rows, instance_id, condition, "visible")
            private_conditions[condition] = shuffled(private_rows, instance_id, condition, "private")

        question = shuffled(focal_visible, instance_id, "boundary_question")
        visible_payload = {
            "schema_version": 1, "instance_id": instance_id,
            "task_id": task["id"], "target_world": target_world,
            "comparison_world": comparison_world, "conditions": visible_conditions,
            "boundary_question": question,
        }
        private_payload = {
            "schema_version": 1, "instance_id": instance_id,
            "task_id": task["id"], "target_world": target_world,
            "comparison_world": comparison_world,
            "conditions": private_conditions,
            "boundary_truth": [{
                "conflict_id": row["conflict_id"],
                "mechanism_id": row["current_private"]["mechanism_id"],
                "truth": row["target_truth"],
                "candidate_text_sha256": row["stale_private"]["text_sha256"],
            } for row in focal_private],
        }
        visible_path = VISIBLE / f"{instance_id}.json"
        private_path = PRIVATE / f"{instance_id}.json"
        visible_path.write_text(json.dumps(visible_payload, indent=2, sort_keys=True) + "\n")
        private_path.write_text(json.dumps(private_payload, indent=2, sort_keys=True) + "\n")
        manifest_rows.append({
            "instance_id": instance_id, "task_id": task["id"],
            "target_world": target_world, "comparison_world": comparison_world,
            "conditions": 5, "focal_candidates": len(focal_private),
            "stale_additions": len(stale),
            "visible_path": str(visible_path.relative_to(ROOT)),
            "visible_sha256": sha256(visible_path),
            "evaluator_path": str(private_path.relative_to(ROOT)),
            "evaluator_sha256": sha256(private_path),
        })

    manifest = {
        "schema_version": 1, "seed": SEED,
        "directed_instances": len(manifest_rows),
        "condition_instances": 5 * len(manifest_rows),
        "rows": manifest_rows,
    }
    MANIFEST.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    tracked = [
        ROOT / "prereg/ext_v2_cds_20261016.md", TASK_PATH, FAMILY_PATH,
        ROOT / "config/ext_v2/diagnostic_packages.json",
        ROOT / "config/ext_v2/web_cells.json",
        ROOT / "reports/paperA/ext_v2/ext_v2_screen.csv",
        ROOT / "reports/paperA/ext_v2/oracle_validation.json",
        ROOT / "reports/paperA/ext_v2/setup_summary.json",
        ROOT / "data/conflicts.jsonl", ROOT / "config/resource_model.json",
        ROOT / "scripts/build_ext_v2_cds_20261016.py", Path(__file__), MANIFEST,
        *[Path(row["visible_path"]) if Path(row["visible_path"]).is_absolute()
          else ROOT / row["visible_path"] for row in manifest_rows],
        *[ROOT / row["evaluator_path"] for row in manifest_rows],
    ]
    freeze = {
        "schema_version": 1, "rule_commit": "a66712a5f0414b4f382fa6de513fd61fefb61d89",
        "tasks": len(tasks), "families": len({task["independent_family_id"] for task in tasks}),
        "directed_cds_instances": len(manifest_rows),
        "cds_condition_instances": 5 * len(manifest_rows),
        "inputs": {str(path.relative_to(ROOT)): sha256(path) for path in tracked},
    }
    FREEZE.write_text(json.dumps(freeze, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "tasks": len(tasks), "families": freeze["families"],
        "directed_instances": len(manifest_rows),
        "condition_instances": 5 * len(manifest_rows),
        "manifest_sha256": sha256(MANIFEST), "freeze_sha256": sha256(FREEZE),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
