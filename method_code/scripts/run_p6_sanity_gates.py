"""Evaluator-only G1/G2 gates for the P6 pipeline amendment."""
from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter, load_webcraft_module
from arbiter.agent import ArbiterAgent
from arbiter.agent.aggregation import (
    SearchDerivedClaim,
    naive_rag,
    operator_from_assertion,
    shared_planner,
)
from arbiter.contracts import Goal, ItemStack
from arbiter.pinning import verify_webcraft_pin


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_WEBCRAFT = Path("/root/autodl-tmp/webcraft_arbiter_latest")
WORLDS = ("1.16.5", "1.19", "1.21.11")
DEVELOPMENT_TARGETS = {
    "eff_007": {"item": "chest_minecart", "count": 1},
    "fea_002": {"item": "bundle", "count": 1},
    "fea_009": {"item": "iron_ore", "count": 1},
    "pla_001": {"item": "lead", "count": 1},
    "pla_003": {"item": "wooden_spear", "count": 1},
    "pla_008": {"item": "oak_chest_boat", "count": 1},
    "pla_009": {"item": "compass", "count": 1},
    "pla_011": {"item": "hopper_minecart", "count": 1},
}
INITIAL = Counter({
    "wooden_pickaxe": 1,
    "stick": 2,
    "cobblestone": 2,
    "leather": 8,
    "string": 8,
    "paper": 8,
    "chiseled_stone_bricks": 8,
})


def _webcraft_oracle(webcraft: Path):
    sys.path.insert(0, str(webcraft))
    sys.path.insert(0, str(webcraft / "scripts"))
    from conflict_core import assertion_id, recipe_assertion  # type: ignore
    from oracle.planner import OraclePlanner  # type: ignore

    return OraclePlanner, assertion_id, recipe_assertion


def _gold_pipeline(source_world: str, webcraft: Path):
    OraclePlanner, assertion_id, recipe_assertion = _webcraft_oracle(webcraft)
    oracle = OraclePlanner(source_world, initial=INITIAL)
    operators: dict[str, Any] = {}
    claims = []
    for recipe in oracle.recipes:
        assertion = recipe_assertion(recipe["raw"])
        if assertion is None:
            continue
        operator = operator_from_assertion(assertion)
        operators[operator.mechanism_id] = operator
    for source in oracle.sources.values():
        assertion = {
            "kind": "drop",
            "block": source["block"],
            "silk_touch": False,
            "drops": [source["output"]],
            "_execution": {
                "requires": [source["tool"]] if source["tool"] else [],
                "output_count": source["output_count"],
            },
        }
        operator = operator_from_assertion(assertion)
        identity_assertion = {key: value for key, value in assertion.items() if key != "_execution"}
        if operator.mechanism_id != assertion_id(identity_assertion):
            raise AssertionError("gold drop normalization mismatch")
        operators[operator.mechanism_id] = operator
    for index, mechanism in enumerate(operators):
        hit = {
            "text": "evaluator gold assertion",
            "source_site": "evaluator_gold",
            "page_title": mechanism,
            "revid": index,
            "timestamp": "9999-12-31T23:59:59Z",
            "chunk_id": f"gold_{index}",
        }
        claims.append(SearchDerivedClaim(hit, mechanism, True))
    return oracle, list(operators.values()), naive_rag(claims)


def _execute(
    *, task_id: str, execution_world: str, source_world: str,
    webcraft: Path, output_dir: Path,
) -> dict[str, Any]:
    OraclePlanner, _, _ = _webcraft_oracle(webcraft)
    target = DEVELOPMENT_TARGETS[task_id]
    source_oracle, operators, aggregation = _gold_pipeline(source_world, webcraft)
    current_oracle = OraclePlanner(execution_world, initial=INITIAL).plan(
        target["item"], target["count"],
    )
    source_plan = source_oracle.plan(target["item"], target["count"])
    plans = shared_planner(
        aggregation, operators, INITIAL, target["item"], target["count"], limit=1,
    )
    log_path = output_dir / "actions" / f"{task_id}__{source_world}__in__{execution_world}.jsonl"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    if log_path.exists():
        log_path.unlink()
    sim = load_webcraft_module("sim", root=webcraft, pin_path=ROOT / "config/webcraft_pin.json")
    handle = sim.create_simulated_agent(
        version=execution_world,
        log_path=log_path,
        initial_inventory=INITIAL,
    )
    api = PublicSemanticAdapter(handle["api"])
    agent = ArbiterAgent(
        Goal(target["item"], target["count"]),
        [ItemStack(name, count) for name, count in INITIAL.items()],
        api,
    )
    agent.configure(operators, aggregation.belief)
    try:
        if plans:
            execution_success = agent.execute_plan(plans[0], exec_as_probe=False)
            reported_feasible = True
        else:
            execution_success = False
            reported_feasible = False
        api.submit({"feasible": reported_feasible})
        final_inventory = Counter({row["name"]: row["count"] for row in api.inventory()})
        handle["evaluator"].finish_episode()
    finally:
        handle["shutdown"]()
    events = [json.loads(line) for line in log_path.read_text().splitlines() if line]
    produced = final_inventory[target["item"]] >= target["count"]
    success = bool(
        (current_oracle.feasible and reported_feasible and produced)
        or (not current_oracle.feasible and not reported_feasible)
    )
    primary_cost = sum(int(event["cost"]["primary"]) for event in events)
    action_methods = [
        event["method"] for event in events
        if event["method"] in {"mine", "craft_grid", "smelt", "check_name"}
    ]
    return {
        "task_id": task_id,
        "source_world": source_world,
        "execution_world": execution_world,
        "target": target,
        "current_feasible": current_oracle.feasible,
        "source_feasible": source_plan.feasible,
        "reported_feasible": reported_feasible,
        "execution_success": execution_success,
        "produced_goal": produced,
        "success": success,
        "primary_cost": primary_cost,
        "oracle_cost": current_oracle.primary_cost,
        "cost_match": (
            primary_cost == current_oracle.primary_cost
            if current_oracle.feasible and success else not current_oracle.feasible and success
        ),
        "planned_steps": len(plans[0].steps) if plans else 0,
        "action_methods": action_methods,
        "source_oracle_actions": source_plan.actions,
        "current_oracle_actions": current_oracle.actions,
        "log_path": str(log_path.relative_to(ROOT)),
    }


def _development_expectations(path: Path) -> dict[str, dict[str, Any]]:
    selected = {}
    for line in path.read_text().splitlines():
        row = json.loads(line)
        if row.get("task_id") not in DEVELOPMENT_TARGETS:
            continue
        if row.get("split") != "dev":
            raise RuntimeError("selected stale expectation is not development data")
        selected[row["task_id"]] = row
    if set(selected) != set(DEVELOPMENT_TARGETS):
        raise RuntimeError("stale expectations do not cover exactly eight development tasks")
    return selected


def run_gates(webcraft: Path, output_dir: Path, label: str) -> dict[str, Any]:
    verify_webcraft_pin(ROOT / "config/webcraft_pin.json", webcraft, require_ready=True)
    g1_rows = []
    for world in WORLDS:
        for task_id in sorted(DEVELOPMENT_TARGETS):
            g1_rows.append(_execute(
                task_id=task_id,
                execution_world=world,
                source_world=world,
                webcraft=webcraft,
                output_dir=output_dir / label / "g1",
            ))
    g1_passed = all(row["success"] and row["cost_match"] for row in g1_rows)
    g1 = {
        "gate": "G1",
        "pairs": len(g1_rows),
        "SR": sum(row["success"] for row in g1_rows) / len(g1_rows),
        "HTR": 0.0,
        "cost_matches": sum(row["cost_match"] for row in g1_rows),
        "passed": g1_passed,
        "rows": g1_rows,
    }

    expectations = _development_expectations(
        Path("/root/autodl-tmp/webcraft/data/stale_agent_expectations.jsonl"),
    )
    g2_rows = []
    for task_id in sorted(DEVELOPMENT_TARGETS):
        row = _execute(
            task_id=task_id,
            execution_world="1.21.11",
            source_world="1.16.5",
            webcraft=webcraft,
            output_dir=output_dir / label / "g2",
        )
        expected = expectations[task_id]
        observed_stale = bool(
            (not row["source_feasible"] and row["current_feasible"])
            or not row["execution_success"]
            or row["source_oracle_actions"] != row["current_oracle_actions"]
        )
        row.update({
            "observed_stale_action": observed_stale,
            "expected_stale_action": expected["expected_stale_action"],
            "expected_success": expected["expected_success"],
            "classification_match": (
                observed_stale == expected["expected_stale_action"]
                and row["success"] == expected["expected_success"]
            ),
        })
        g2_rows.append(row)
    g2_htr = sum(row["observed_stale_action"] for row in g2_rows) / len(g2_rows)
    g2 = {
        "gate": "G2",
        "tasks": len(g2_rows),
        "HTR": g2_htr,
        "classification_matches": sum(row["classification_match"] for row in g2_rows),
        "passed": all(row["classification_match"] for row in g2_rows) and g2_htr > 0,
        "rows": g2_rows,
    }
    result = {
        "schema_version": 1,
        "label": label,
        "development_task_ids": sorted(DEVELOPMENT_TARGETS),
        "evaluation_tasks_read_or_run": False,
        "llm_calls": 0,
        "g1": g1,
        "g2": g2,
        "passed": g1["passed"] and g2["passed"],
    }
    target = output_dir / f"{label}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--webcraft-root", type=Path, default=DEFAULT_WEBCRAFT)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/p6_gates"))
    parser.add_argument("--label", default="baseline")
    args = parser.parse_args()
    result = run_gates(
        args.webcraft_root.resolve(), args.output_dir.resolve(), args.label,
    )
    print(json.dumps({
        "passed": result["passed"],
        "G1": {key: result["g1"][key] for key in ("passed", "SR", "HTR", "cost_matches")},
        "G2": {key: result["g2"][key] for key in ("passed", "HTR", "classification_matches")},
    }, sort_keys=True))
    raise SystemExit(0 if result["passed"] else 2)
