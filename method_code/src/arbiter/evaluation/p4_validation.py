from __future__ import annotations

import json
from collections import Counter
from pathlib import Path
from typing import Any

from arbiter.agent.decision import ContextBelief, DecisionController
from arbiter.agent.planning import BeliefPlanner, CandidatePlan, MechanismOperator
from arbiter.fairness import scan_agent_tree

from .operator_compiler import compile_world_statements, certain_belief


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def validate_correct_statements(root: Path) -> dict[str, Any]:
    tasks = [task for task in _jsonl(root / "data/tasks.jsonl") if task["split"] == "dev"]
    checked = matched = 0
    failures = []
    cache = {release: compile_world_statements(root, release) for release in ("1.16.5", "1.19", "1.21.11")}
    for task in tasks:
        for release, expected in task["feasible"].items():
            checked += 1
            initial_items = task["standard_items"] + task.get("task_supplements", [])
            initial = Counter({item["name"]: item["count"] for item in initial_items})
            planner = BeliefPlanner(cache[release], initial, action_limit=task["action_budget"])
            plans = planner.candidates(task["target"]["item"], task["target"]["count"], 1)
            actual = bool(plans)
            if actual == expected:
                matched += 1
            else:
                failures.append({"task_id": task["id"], "release": release,
                                 "expected": expected, "actual": actual})
    return {"cases": checked, "matched": matched, "agreement": matched / checked, "failures": failures}


def validate_statement_rejection(root: Path) -> dict[str, Any]:
    old = [operator for operator in compile_world_statements(root, "1.16.5")
           if operator.method == "mine" and operator.params.get("node_type") == "iron_ore"]
    initial_items = json.loads((root / "config/resource_model.json").read_text())["standard_inventory"]
    # Supply the tool through the public initial kit for this isolated intervention test.
    initial_items = initial_items + [{"name": "stone_pickaxe", "count": 1}]
    initial = Counter({item["name"]: item["count"] for item in initial_items})
    plans = BeliefPlanner(old, initial).candidates("iron_ore", 1, 1)
    belief = certain_belief(old)
    initial_feasible = bool(plans)
    belief.update(old[0].mechanism_id, False)
    corrected_probability = belief.mechanism_probability(old[0].mechanism_id)
    corrected_plans = BeliefPlanner([
        MechanismOperator(**{**old[0].__dict__, "probability": corrected_probability})
    ], initial).candidates("iron_ore", 1, 1)
    corrected_feasible = bool(corrected_plans)
    return {
        "initial_feasible": initial_feasible,
        "corrected_feasible": corrected_feasible,
        "corrected_mechanism_probability": corrected_probability,
        "posterior_context_mass": belief.q.tolist(),
        "passed": initial_feasible and not corrected_feasible
        and corrected_probability == 0.0,
    }


def validate_universal_plan() -> dict[str, Any]:
    plan = CandidatePlan((), (), 0, 0.0, ())
    belief = ContextBelief([0.2, 0.3, 0.5], {})
    decision = DecisionController().choose([plan], belief, {})
    return {"decision": decision.kind, "passed": decision.kind == "execute"}


def run(root: Path, agent_root: Path, adapter_root: Path | None = None) -> dict[str, Any]:
    correct = validate_correct_statements(root)
    rejection = validate_statement_rejection(root)
    universal = validate_universal_plan()
    fairness = scan_agent_tree(agent_root)
    if adapter_root is not None:
        fairness.extend(scan_agent_tree(adapter_root))
    return {
        "correct_statements": correct,
        "statement_rejection": rejection,
        "universal_plan": universal,
        "fairness_violations": fairness,
        "passed": correct["agreement"] == 1.0 and rejection["passed"]
        and universal["passed"] and not fairness,
    }
