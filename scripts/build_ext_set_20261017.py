#!/usr/bin/env python3
"""Build and validate the preregistered post-review EXT diagnostic set."""
from __future__ import annotations

import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oracle.planner import OraclePlanner, VERSIONS, validate_plan  # noqa: E402
from sim.semantic_agent import create_simulated_agent  # noqa: E402


AUTHENTIC_MECHANISMS = (
    "recipe:gold_ingot",
    "recipe:iron_ingot",
    "recipe:raw_gold_block",
    "recipe:raw_iron_block",
)
TASK_SPECS = (
    {
        "id": "ext_iron_ingot",
        "mechanism": "recipe:iron_ingot",
        "target": "iron_ingot",
        "package": {"coal": 1, "raw_iron": 1},
        "distinguishing_input": "raw_iron",
    },
    {
        "id": "ext_gold_ingot",
        "mechanism": "recipe:gold_ingot",
        "target": "gold_ingot",
        "package": {"coal": 1, "iron_pickaxe": 1, "raw_gold": 1},
        "distinguishing_input": "raw_gold",
    },
    {
        "id": "ext_raw_iron_block",
        "mechanism": "recipe:raw_iron_block",
        "target": "raw_iron_block",
        "package": {"raw_iron": 9},
        "distinguishing_input": "raw_iron",
    },
    {
        "id": "ext_raw_gold_block",
        "mechanism": "recipe:raw_gold_block",
        "target": "raw_gold_block",
        "package": {"raw_gold": 9},
        "distinguishing_input": "raw_gold",
    },
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def ingredient_options(assertion: dict[str, Any]) -> set[str]:
    ingredients = {
        str(item)
        for group in assertion.get("ingredients", [])
        for item in group
    }
    for row in assertion.get("grid", []):
        for group in row:
            ingredients.update(map(str, group))
    return ingredients


def selected_conflict(spec: dict[str, Any], conflicts: list[dict[str, Any]]) -> dict[str, Any]:
    matches = []
    for row in conflicts:
        assertion = row["assertion"]
        if assertion.get("output") != spec["target"]:
            continue
        if spec["target"].endswith("_ingot"):
            if (assertion.get("kind") == "recipe"
                    and assertion.get("station") == "furnace"
                    and ingredient_options(assertion) == {spec["distinguishing_input"]}):
                matches.append(row)
        elif (assertion.get("kind") == "recipe"
              and assertion.get("station") == "crafting_grid"
              and ingredient_options(assertion) == {spec["distinguishing_input"]}):
            matches.append(row)
    if len(matches) != 1:
        raise RuntimeError(f"{spec['id']} expected one executable conflict, got {len(matches)}")
    return matches[0]


def initial_inventory(standard: list[dict[str, Any]], package: dict[str, int]) -> Counter[str]:
    result = Counter({row["name"]: int(row["count"]) for row in standard})
    result.update(package)
    return result


def execute_plan(task: dict[str, Any], world: str) -> dict[str, Any]:
    initial = initial_inventory(task["standard_items"], {
        row["name"]: int(row["count"]) for row in task["task_supplements"]
    })
    handle = create_simulated_agent(version=world, initial_inventory=initial)
    api = handle["api"]
    arena = json.loads((ROOT / "harness/config/arena.json").read_text())
    try:
        plan = task["oracle_plans"][world]
        actions = []
        if plan:
            for action in plan["actions"]:
                if action["method"] == "mine":
                    nodes = [
                        row for row in arena["resources"]
                        if row["block"] == action["node_type"]
                    ]
                    result = api.mine(nodes[action["node_ordinal"] - 1]["nodeId"])
                elif action["method"] == "craft_grid":
                    result = api.craft_grid(action["layout"])
                elif action["method"] == "smelt":
                    result = api.smelt(action["input"], action["fuel"], action["n"])
                else:
                    raise RuntimeError(f"unsupported oracle action {action['method']}")
                actions.append({"method": action["method"], "result": result})
                if result.get("success") is False:
                    break
        inventory = api.inventory()
        count = sum(
            int(row["count"]) for row in inventory
            if row["name"] == task["target"]["item"]
        )
        produced = count >= int(task["target"]["count"])
        expected = bool(task["feasible"][world])
        passed = produced if expected else plan is None
        return {
            "task_id": task["id"],
            "world": world,
            "expected_feasible": expected,
            "produced_goal": produced,
            "actions": len(actions),
            "passed": passed and all(
                row["result"].get("success") is not False for row in actions
            ),
        }
    finally:
        handle["shutdown"]()


def tracked_inputs() -> list[Path]:
    paths = [
        ROOT / "reports/expansion_probe/mechanism_authenticity.csv",
        ROOT / "reports/expansion_probe/mechanism_authenticity_summary.json",
        ROOT / "config/resource_model.json",
        ROOT / "data/conflicts.jsonl",
        ROOT / "truth/1.16.5.json",
        ROOT / "truth/1.19.json",
        ROOT / "truth/1.21.11.json",
        ROOT / "sim/semantic_agent.py",
        ROOT / "scripts/build_ext_set_20261017.py",
        ROOT / "scripts/run_ext_set_20261017.py",
        Path("/root/autodl-tmp/arbiter/artifacts/prior/frozen_prior_bundle.json"),
        Path("/root/autodl-tmp/arbiter/config/arbiter_hparams.json"),
        Path("/root/autodl-tmp/arbiter/config/webcraft_pin.json"),
        Path("/root/autodl-tmp/arbiter/src/arbiter/evaluation/p6_execution.py"),
    ]
    for base in (ROOT / "corpus/indexes/formal_p1", ROOT / "corpus/claims_cache/formal_p1"):
        for condition in (
            "matched_1.16.5", "matched_1.19", "matched_1.21.11",
            "mismatched_old2new", "mismatched_new2old",
        ):
            paths.extend(sorted(path for path in (base / condition).iterdir() if path.is_file()))
    return paths


def main() -> int:
    summary = json.loads(
        (ROOT / "reports/expansion_probe/mechanism_authenticity_summary.json").read_text()
    )
    if tuple(sorted(summary["behaviorally_real_new_mechanisms"])) != tuple(
        sorted(AUTHENTIC_MECHANISMS)
    ):
        raise RuntimeError("behaviorally real candidate set changed")

    resource_model = json.loads((ROOT / "config/resource_model.json").read_text())
    standard = resource_model["standard_inventory"]
    capacities = resource_model["resource_block_counts"]
    conflicts = jsonl(ROOT / "data/conflicts.jsonl")
    tasks = []
    packages = []
    for spec in TASK_SPECS:
        conflict = selected_conflict(spec, conflicts)
        package_rows = [
            {"name": name, "count": count}
            for name, count in sorted(spec["package"].items())
        ]
        initial = initial_inventory(standard, spec["package"])
        plans = {}
        feasible = {}
        costs = {}
        for world in VERSIONS:
            result = OraclePlanner(
                world, capacities=capacities, initial=initial, action_limit=30,
            ).plan(spec["target"])
            if result.feasible:
                validate_plan(result, initial)
            plans[world] = result.as_dict() if result.feasible else None
            feasible[world] = bool(result.feasible)
            costs[world] = result.primary_cost
        task = {
            "id": spec["id"],
            "split": "post_review_ext",
            "type": "diagnostic_extension",
            "post_review_added": True,
            "independent_mechanism_id": spec["mechanism"],
            "conflict_ids": [conflict["id"]],
            "conflicts_by_world": {
                world: [conflict["id"]] if conflict["truth_vector"][world] else []
                for world in VERSIONS
            },
            "target": {"item": spec["target"], "count": 1},
            "standard_items": standard,
            "task_supplements": package_rows,
            "extension_diagnostic_package": {
                "enabled": True,
                "purpose": "isolate the independent mechanism from Core42 mining bottlenecks",
                "items": package_rows,
            },
            "feasible": feasible,
            "oracle_cost": costs,
            "oracle_plans": plans,
            "action_budget": 12,
            "wall_clock_seconds": 300,
        }
        tasks.append(task)
        packages.append({
            "task_id": spec["id"],
            "independent_mechanism_id": spec["mechanism"],
            "items": package_rows,
            "goal_supplied": False,
        })

    data_dir = ROOT / "data/ext"
    config_dir = ROOT / "config"
    report_dir = ROOT / "reports/paperA/ext"
    data_dir.mkdir(parents=True, exist_ok=True)
    report_dir.mkdir(parents=True, exist_ok=True)
    task_path = data_dir / "ext_tasks_20261017.jsonl"
    task_path.write_text("".join(
        json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n" for row in tasks
    ))
    package_path = config_dir / "ext_diagnostic_packages_20261017.json"
    package_path.write_text(json.dumps({
        "schema_version": 1,
        "scope": "post_review_ext_only",
        "not_part_of_core42": True,
        "packages": packages,
    }, indent=2, sort_keys=True) + "\n")

    validation = [execute_plan(task, world) for task in tasks for world in VERSIONS]
    if not all(row["passed"] for row in validation):
        raise RuntimeError("EXT oracle/simulator validation failed")
    validation_path = report_dir / "pre_freeze_task_validation.json"
    validation_path.write_text(json.dumps({
        "schema_version": 1,
        "checks": validation,
        "passed": len(validation),
        "total": len(validation),
    }, indent=2, sort_keys=True) + "\n")

    inputs = {}
    for path in tracked_inputs():
        if not path.is_file():
            raise FileNotFoundError(path)
        key = str(path.relative_to(ROOT)) if path.is_relative_to(ROOT) else str(path)
        inputs[key] = sha256(path)
    inputs[str(task_path.relative_to(ROOT))] = sha256(task_path)
    inputs[str(package_path.relative_to(ROOT))] = sha256(package_path)
    inputs[str(validation_path.relative_to(ROOT))] = sha256(validation_path)
    manifest_path = config_dir / "ext_freeze_manifest_20261017.json"
    manifest_path.write_text(json.dumps({
        "schema_version": 1,
        "task_count": len(tasks),
        "independent_mechanism_count": len(AUTHENTIC_MECHANISMS),
        "method_run_episodes": 4 * 5 * 10,
        "inputs": dict(sorted(inputs.items())),
    }, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "tasks": len(tasks),
        "mechanisms": len(AUTHENTIC_MECHANISMS),
        "validation": f"{len(validation)}/{len(validation)}",
        "task_sha256": sha256(task_path),
        "package_sha256": sha256(package_path),
        "manifest_sha256": sha256(manifest_path),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
