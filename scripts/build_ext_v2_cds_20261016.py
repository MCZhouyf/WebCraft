#!/usr/bin/env python3
"""Deterministically screen and freeze the W7a EXT-v2/CDS setup."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARBITER = Path("/root/autodl-tmp/arbiter")
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ARBITER / "src")]

from audit_mechanism_authenticity import (  # noqa: E402
    compact, evaluate_action, public_action, recipe_actions, representation_source,
    requirements_for, terminal_key,
)
from oracle.planner import INITIAL, VERSIONS, OraclePlanner, validate_plan  # noqa: E402
from scripts.build_ext_set_20261017 import execute_plan  # noqa: E402
from sim.semantic_agent import create_simulated_agent  # noqa: E402


W5 = ROOT / "reports/paperA/w5/ext_candidates.csv"
MAIN = ROOT / "reports/paperA/w5/independent_mechanism_evidence.csv"
W6_TASKS = ROOT / "data/ext/ext_tasks_20261017.jsonl"
OUT = ROOT / "reports/paperA/ext_v2"
TASK_PATH = ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl"
PACKAGE_PATH = ROOT / "config/ext_v2/diagnostic_packages.json"
FAMILY_PATH = ROOT / "config/ext_v2/mechanism_families.json"
CELL_PATH = ROOT / "config/ext_v2/web_cells.json"
WORLDS = tuple(VERSIONS)
SEED = 20261016


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError(f"refusing empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def assertion_subject(assertion: dict[str, Any]) -> str:
    return str(
        assertion.get("output") or assertion.get("item") or assertion.get("block") or ""
    )


def family_key(mechanism: str, subject: str) -> str:
    if subject.endswith("_spear"):
        return "feature:spear_family"
    if subject.endswith("_harness"):
        return "feature:harness_family"
    if subject in {"raw_iron", "raw_gold", "raw_iron_block", "raw_gold_block"}:
        return "feature:raw_material_introduction"
    if subject in {"iron_ingot", "gold_ingot"}:
        return "feature:raw_ingot_smelting"
    if subject.startswith("wooden_") and subject.rsplit("_", 1)[-1] in {
        "axe", "hoe", "pickaxe", "shovel", "sword"
    }:
        return "feature:wooden_tool_recipe_family"
    if subject.startswith("stone_") and subject.rsplit("_", 1)[-1] in {
        "axe", "hoe", "pickaxe", "shovel", "sword"
    }:
        return "feature:stone_tool_recipe_family"
    if subject in {"blue_wool", "blue_carpet"}:
        return "feature:blue_wool_chain"
    rename = {
        "grass_path": "rename:grass_path_dirt_path",
        "dirt_path": "rename:grass_path_dirt_path",
        "grass": "rename:grass_short_grass",
        "short_grass": "rename:grass_short_grass",
        "scute": "rename:scute_turtle_scute",
        "turtle_scute": "rename:scute_turtle_scute",
        "chain": "rename:chain_iron_chain",
        "iron_chain": "rename:chain_iron_chain",
    }
    if subject in rename:
        return rename[subject]
    return f"item:{subject}"


def source_rows(conflicts: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for raw in read_csv(W5):
        rows.append({
            "source_pool": "w5_104",
            "source_id": raw["conflict_id"],
            "mechanism": raw["independent_mechanism_id"],
            "conflict_ids": [raw["conflict_id"]],
            "coverage_hint": raw["claim_coverage_class"],
            "claim_T1_hint": raw["claim_T1"],
            "claim_T2_hint": raw["claim_T2"],
            "claim_T3_hint": raw["claim_T3"],
        })
    for raw in read_csv(MAIN):
        ids = raw["conflict_ids"].split(";")
        rows.append({
            "source_pool": "main_7",
            "source_id": raw["mechanism_id"],
            "mechanism": raw["mechanism_id"],
            "conflict_ids": ids,
            "coverage_hint": "",
            "claim_T1_hint": "",
            "claim_T2_hint": "",
            "claim_T3_hint": "",
        })
    for task in jsonl(W6_TASKS):
        rows.append({
            "source_pool": "w6_4",
            "source_id": task["independent_mechanism_id"],
            "mechanism": task["independent_mechanism_id"],
            "conflict_ids": task["conflict_ids"],
            "coverage_hint": "",
            "claim_T1_hint": "",
            "claim_T2_hint": "",
            "claim_T3_hint": "",
        })
    if len(rows) != 115:
        raise RuntimeError(f"candidate universe changed: {len(rows)} != 115")
    for row in rows:
        missing = [conflict_id for conflict_id in row["conflict_ids"] if conflict_id not in conflicts]
        if missing:
            raise RuntimeError(f"missing candidate conflicts: {missing}")
    return rows


def probe_action(row: dict[str, Any]) -> dict[str, Any] | None:
    probe = row.get("probe")
    if not probe:
        return None
    params = probe.get("params", {})
    if probe["method"] == "craft_grid":
        return {"method": "craft_grid", "layout": params["layout"]}
    if probe["method"] == "smelt":
        return {
            "method": "smelt", "input": params["input"],
            "fuel": params["fuel"], "n": int(params["n"]),
        }
    if probe["method"] == "mine":
        return {
            "method": "mine", "node_type": params["node_type"], "node_ordinal": 1,
        }
    return None


def assertion_action(
    assertion: dict[str, Any], planners: dict[str, OraclePlanner]
) -> dict[str, Any] | None:
    """Construct one canonical concrete action from this exact assertion."""
    kind = assertion["kind"]
    if kind == "concrete_grid":
        return {"method": "craft_grid", "layout": assertion["layout"]}
    if kind != "recipe" or assertion.get("station") not in {"crafting_grid", "furnace"}:
        return None
    reachable = set().union(*(planner.reachable for planner in planners.values()))
    if assertion.get("ordered"):
        cells = [cell for row in assertion.get("grid", []) for cell in row]
        choices = []
        for cell in cells:
            if not cell:
                choices.append(None)
                continue
            valid = sorted(item for item in cell if item in reachable)
            if not valid:
                return None
            choices.append(valid[0])
        return {"method": "craft_grid", "layout": choices + [None] * (9 - len(choices))}
    ingredients = []
    for slot in assertion.get("ingredients", []):
        valid = sorted(item for item in slot if item in reachable)
        if not valid:
            return None
        ingredients.append(valid[0])
    if assertion["station"] == "furnace":
        if len(ingredients) != 1:
            return None
        return {"method": "smelt", "input": ingredients[0], "fuel": "coal", "n": 1}
    return {"method": "craft_grid", "layout": ingredients + [None] * (9 - len(ingredients))}


def check_name_outcomes(item: str) -> dict[str, Any]:
    outcomes = {}
    for world in WORLDS:
        handle = create_simulated_agent(version=world, initial_inventory=Counter(INITIAL))
        try:
            result = handle["api"].check_name(item)
            outcomes[world] = {
                "requirements_feasible": True,
                "success": True,
                "products": [],
                "resolvable": bool(result["resolvable"]),
                "error_category": None,
            }
        finally:
            handle["shutdown"]()
    return outcomes


def craftability_outcomes(item: str, planners: dict[str, OraclePlanner]) -> tuple[dict[str, Any], dict[str, Any]]:
    outcomes, plans = {}, {}
    for world in WORLDS:
        plan = planners[world].plan(item)
        if plan.feasible:
            validate_plan(plan)
        plans[world] = plan
        outcomes[world] = {
            "requirements_feasible": True,
            "success": bool(plan.feasible),
            "products": ([{"name": item, "count": 1}] if plan.feasible else []),
            "error_category": None if plan.feasible else "goal_infeasible",
        }
    return outcomes, plans


def audit_source(
    source: dict[str, Any], conflicts: dict[str, dict[str, Any]],
    planners: dict[str, OraclePlanner], arena: dict[str, Any],
) -> dict[str, Any]:
    exact = [conflicts[conflict_id] for conflict_id in source["conflict_ids"]]
    assertion = exact[0]["assertion"]
    subject = assertion_subject(assertion)
    kind = assertion["kind"]
    evaluations: list[dict[str, Any]] = []
    plans: dict[str, Any] | None = None
    if kind == "name":
        outcomes = check_name_outcomes(subject)
        action = {"method": "check_name", "name": subject}
        selected = {
            "action": action, "requirements": {}, "outcomes": outcomes,
            "distinguishes": len({compact(value) for value in outcomes.values()}) >= 2,
            "source_version": min(WORLDS), "primary_cost": 1,
            "witness": [action], "prerequisites": {},
        }
        evaluations = [selected]
    elif kind == "craftability":
        outcomes, plans = craftability_outcomes(subject, planners)
        feasible = [world for world in WORLDS if plans[world].feasible]
        selected_world = min(
            feasible,
            key=lambda world: (plans[world].primary_cost, len(plans[world].actions), world),
        ) if feasible else min(WORLDS)
        selected_plan = plans[selected_world]
        selected = {
            "action": public_action(selected_plan.actions[-1]) if selected_plan.feasible else {
                "method": "task_goal", "item": subject,
            },
            "requirements": {}, "outcomes": outcomes,
            "distinguishes": len({compact(value) for value in outcomes.values()}) >= 2,
            "source_version": selected_world,
            "primary_cost": selected_plan.primary_cost if selected_plan.feasible else 0,
            "witness": ([public_action(action) for action in selected_plan.actions]
                        if selected_plan.feasible else []),
            "prerequisites": {},
        }
        evaluations = [selected]
    else:
        fixed = [probe_action(row) or assertion_action(row["assertion"], planners) for row in exact]
        actions = {terminal_key(action): action for action in fixed if action is not None}
        if not actions:
            for action in recipe_actions(planners, subject):
                actions[terminal_key(action)] = action
        if not actions:
            raise RuntimeError(f"no executable action for {source['source_id']}")
        evaluations = [evaluate_action(planners, action, arena) for action in actions.values()]
        distinguishing = [row for row in evaluations if row["distinguishes"]]
        selected = min(
            distinguishing or evaluations,
            key=lambda row: (
                row["primary_cost"], len(row["witness"]), row["source_version"],
                terminal_key(row["action"]),
            ),
        )
    behavioral = bool(selected["distinguishes"])
    if behavioral:
        rep_source, rep_detail = "", ""
    else:
        rep_source, rep_detail = representation_source(
            [row["assertion"] for row in exact], planners
        )
    return {
        **source,
        "subject": subject,
        "assertion_kind": kind,
        "family_id": family_key(source["mechanism"], subject),
        "behavior_decision": "behaviorally_real" if behavioral else "representational_difference",
        "representation_source": rep_source,
        "representation_detail": rep_detail,
        "actions_checked": len(evaluations),
        "selected_action": selected["action"],
        "selected_requirements": selected.get("requirements", {}),
        "witness": selected["witness"],
        "witness_cost": selected["primary_cost"],
        "outcomes": selected["outcomes"],
        "truth_vectors": [row["truth_vector"] for row in exact],
        "task_eligible": bool(behavioral and kind != "name"),
        "task_ineligible_reason": (
            "" if behavioral and kind != "name" else
            "planning_interface_unrepresentable" if behavioral else
            "not_behaviorally_real"
        ),
    }


def coverage_inventory(conflicts: dict[str, dict[str, Any]]) -> dict[str, dict[str, Any]]:
    sys.path.insert(0, str(ARBITER / "src"))
    from arbiter.evaluation.p6_execution import _claims, _compiler

    normalizer, _ = _compiler(ROOT)
    mapping = {}
    cells = {
        "T1": "matched_1.16.5", "T2": "matched_1.19", "T3": "matched_1.21.11",
    }
    for timepoint, cell in cells.items():
        rows = jsonl(ROOT / f"corpus/claims_cache/formal_p1/{cell}/chunk_claims.jsonl")
        chunk_ids = sorted({str(row["chunk_id"]) for row in rows})
        bundle = {
            "search_rows": [{"chunk_id": chunk_id, "text": ""} for chunk_id in chunk_ids],
            "claims": rows,
        }
        derived, _ = _claims(bundle, normalizer=normalizer)
        for conflict_id in {row.mechanism_id for row in derived}:
            if conflict_id in conflicts:
                mapping.setdefault(conflict_id, {})[timepoint] = True
    coverage_path = ROOT / "reports/corpus_stats/coverage_p1.csv"
    if coverage_path.is_file():
        for row in read_csv(coverage_path):
            conflict_id = row["conflict_id"]
            old_count = int(row.get("mixed_old_count") or 0)
            new_count = int(row.get("mixed_new_count") or 0)
            if old_count:
                mapping.setdefault(conflict_id, {})["T1"] = True
            if new_count:
                mapping.setdefault(conflict_id, {})["T3"] = True
            if old_count or new_count:
                mapping.setdefault(conflict_id, {})["T2"] = True
    return mapping


def classify_coverage(audit: dict[str, Any], inventory: dict[str, dict[str, Any]]) -> tuple[dict[str, int], str]:
    exists = {timepoint: 0 for timepoint in ("T1", "T2", "T3")}
    hints = {
        "T1": audit.get("claim_T1_hint", ""),
        "T2": audit.get("claim_T2_hint", ""),
        "T3": audit.get("claim_T3_hint", ""),
    }
    for timepoint in exists:
        if str(hints[timepoint]).strip() not in {"", "0", "False", "false"}:
            exists[timepoint] = 1
        if any(inventory.get(conflict_id, {}).get(timepoint) for conflict_id in audit["conflict_ids"]):
            exists[timepoint] = 1
    hint = audit.get("coverage_hint", "")
    if hint in {"two_sided", "one_sided", "missing"}:
        coverage = hint
    else:
        count = sum(exists.values())
        coverage = "two_sided" if count >= 2 else "one_sided" if count == 1 else "missing"
    return exists, coverage


def plan_signature(plan: Any) -> str:
    if not plan.feasible:
        return "infeasible"
    return compact([public_action(action) for action in plan.actions])


def build_task_candidate(
    audit: dict[str, Any], family_audits: list[dict[str, Any]],
    conflicts: dict[str, dict[str, Any]], resource: dict[str, Any],
) -> dict[str, Any] | None:
    if not audit["task_eligible"]:
        return None
    target = audit["subject"]
    action = audit["selected_action"]
    if action.get("method") == "task_goal":
        feasible_plans = []
        capacities = resource["resource_block_counts"]
        for world in WORLDS:
            plan = OraclePlanner(world, capacities=capacities).plan(target)
            if plan.feasible:
                feasible_plans.append((plan.primary_cost, len(plan.actions), world, plan))
        if not feasible_plans:
            return None
        _, _, _, source_plan = min(feasible_plans)
        action = public_action(source_plan.actions[-1])
    requirements = requirements_for(action)
    standard = Counter({row["name"]: int(row["count"]) for row in resource["standard_inventory"]})
    package = Counter({item: max(0, count - standard[item]) for item, count in requirements.items()})
    package = Counter({item: count for item, count in package.items() if count})
    if target in package:
        return None
    initial = standard + package
    plans, feasible, costs = {}, {}, {}
    for world in WORLDS:
        plan = OraclePlanner(
            world, capacities=resource["resource_block_counts"], initial=initial,
            action_limit=30,
        ).plan(target)
        if plan.feasible:
            validate_plan(plan, initial)
        plans[world] = plan
        feasible[world] = bool(plan.feasible)
        costs[world] = plan.primary_cost
    signatures = {plan_signature(plans[world]) for world in WORLDS}
    if len(signatures) < 2:
        return None
    focal_ids = sorted({
        conflict_id for row in family_audits for conflict_id in row["conflict_ids"]
    })
    truth_different = any(
        len({bool(conflicts[conflict_id]["truth_vector"][world]) for world in WORLDS}) > 1
        for conflict_id in focal_ids
    )
    if not truth_different:
        return None
    package_rows = [{"name": item, "count": count} for item, count in sorted(package.items())]
    return {
        "family_id": audit["family_id"],
        "source_id": audit["source_id"],
        "subject": target,
        "coverage": audit["coverage"],
        "focal_conflict_ids": focal_ids,
        "selected_conflict_ids": audit["conflict_ids"],
        "terminal_action": action,
        "package": package_rows,
        "plans": plans,
        "feasible": feasible,
        "costs": costs,
        "max_finite_cost": max(cost for cost in costs.values() if cost is not None),
        "route_signatures": {world: plan_signature(plans[world]) for world in WORLDS},
        "transition_vector": "/".join("1" if feasible[world] else "0" for world in WORLDS),
    }


def make_task(candidate: dict[str, Any], index: int, resource: dict[str, Any], conflicts: dict[str, dict[str, Any]]) -> dict[str, Any]:
    task_id = f"extv2_{index:03d}"
    plans = candidate.pop("plans")
    task = {
        "id": task_id,
        "split": "post_review_ext_v2",
        "type": "diagnostic_extension_v2",
        "post_review_added": True,
        "independent_family_id": candidate["family_id"],
        "independent_mechanism_id": candidate["family_id"],
        "focal_conflict_ids": candidate["focal_conflict_ids"],
        "conflict_ids": candidate["focal_conflict_ids"],
        "conflicts_by_world": {
            world: [conflict_id for conflict_id in candidate["focal_conflict_ids"]
                    if conflicts[conflict_id]["truth_vector"][world]]
            for world in WORLDS
        },
        "target": {"item": candidate["subject"], "count": 1},
        "standard_items": resource["standard_inventory"],
        "task_supplements": candidate["package"],
        "extension_diagnostic_package": {
            "enabled": bool(candidate["package"]),
            "explicit": True,
            "goal_supplied": False,
            "items": candidate["package"],
        },
        "web_coverage": candidate["coverage"],
        "feasible": candidate["feasible"],
        "oracle_cost": candidate["costs"],
        "oracle_plans": {
            world: plans[world].as_dict() if plans[world].feasible else None
            for world in WORLDS
        },
        "action_budget": max(12, 2 * int(candidate["max_finite_cost"]) + 4),
        "wall_clock_seconds": 300,
        "selection_evidence": {
            key: value for key, value in candidate.items()
            if key not in {"feasible", "costs", "package", "focal_conflict_ids"}
        },
    }
    return task


def comparison_pairs(task: dict[str, Any]) -> list[dict[str, str]]:
    signatures = task["selection_evidence"]["route_signatures"]
    output = []
    for i, left in enumerate(WORLDS):
        for right in WORLDS[i + 1:]:
            if signatures[left] == signatures[right] and task["feasible"][left] == task["feasible"][right]:
                continue
            output.extend([
                {"task_id": task["id"], "target_world": left, "comparison_world": right},
                {"task_id": task["id"], "target_world": right, "comparison_world": left},
            ])
    return output


def screen_row(audit: dict[str, Any]) -> dict[str, Any]:
    return {
        "source_pool": audit["source_pool"],
        "source_id": audit["source_id"],
        "mechanism": audit["mechanism"],
        "conflict_ids": ";".join(audit["conflict_ids"]),
        "assertion_kind": audit["assertion_kind"],
        "subject": audit["subject"],
        "family_id": audit["family_id"],
        "behavior_decision": audit["behavior_decision"],
        "representation_source": audit["representation_source"],
        "representation_detail": audit["representation_detail"],
        "actions_checked": audit["actions_checked"],
        "witness_cost": audit["witness_cost"],
        "witness_json": compact(audit["witness"]),
        "selected_action_json": compact(audit["selected_action"]),
        "outcome_1.16.5_json": compact(audit["outcomes"]["1.16.5"]),
        "outcome_1.19_json": compact(audit["outcomes"]["1.19"]),
        "outcome_1.21.11_json": compact(audit["outcomes"]["1.21.11"]),
        "claim_T1": audit["claim_exists"]["T1"],
        "claim_T2": audit["claim_exists"]["T2"],
        "claim_T3": audit["claim_exists"]["T3"],
        "coverage": audit["coverage"],
        "task_eligible": int(audit["task_eligible"]),
        "task_ineligible_reason": audit["task_ineligible_reason"],
        "requires_extension_package": int(bool(audit.get("selected_requirements"))),
    }


def main() -> int:
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit("run with PYTHONHASHSEED=0")
    conflicts_list = jsonl(ROOT / "data/conflicts.jsonl")
    conflicts = {row["id"]: row for row in conflicts_list}
    resource = json.loads((ROOT / "config/resource_model.json").read_text())
    arena = json.loads((ROOT / "harness/config/arena.json").read_text())
    planners = {
        world: OraclePlanner(world, capacities=resource["resource_block_counts"])
        for world in WORLDS
    }
    candidates = source_rows(conflicts)
    audited = [audit_source(row, conflicts, planners, arena) for row in candidates]
    inventory = coverage_inventory(conflicts)
    for row in audited:
        row["claim_exists"], row["coverage"] = classify_coverage(row, inventory)

    families: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in audited:
        families[row["family_id"]].append(row)
    task_candidates: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for family_id, rows in families.items():
        for row in rows:
            built = build_task_candidate(row, rows, conflicts, resource)
            if built is not None:
                task_candidates[family_id].append(built)

    selected = []
    coverage_rank = {"two_sided": 0, "one_sided": 1, "missing": 2}
    for family_id, rows in sorted(task_candidates.items()):
        ranked = sorted(rows, key=lambda row: (
            coverage_rank[row["coverage"]], row["max_finite_cost"],
            len(json.loads(compact(row["terminal_action"]))), row["source_id"], row["subject"],
        ))
        chosen = [ranked[0]]
        for candidate in ranked[1:]:
            if (candidate["subject"] != chosen[0]["subject"]
                    and candidate["transition_vector"] != chosen[0]["transition_vector"]):
                chosen.append(candidate)
                break
        selected.extend(chosen)

    tasks = [make_task(dict(candidate), index, resource, conflicts)
             for index, candidate in enumerate(selected, 1)]
    validation = [execute_plan(task, world) for task in tasks for world in WORLDS]
    failed = [row for row in validation if not row["passed"]]
    if failed:
        raise RuntimeError(f"EXT-v2 oracle/simulator validation failed: {failed[:3]}")

    pairs = [pair for task in tasks for pair in comparison_pairs(task)]
    web_tasks = [task for task in tasks if task["web_coverage"] in {"two_sided", "one_sided"}]
    cells = [
        {"condition": "matched_1.16.5", "world": "1.16.5", "corpus_timepoint": "T1"},
        {"condition": "matched_1.19", "world": "1.19", "corpus_timepoint": "T2"},
        {"condition": "matched_1.21.11", "world": "1.21.11", "corpus_timepoint": "T3"},
        {"condition": "mismatched_old2new", "world": "1.21.11", "corpus_timepoint": "T1"},
        {"condition": "mismatched_new2old", "world": "1.16.5", "corpus_timepoint": "T3"},
    ]

    OUT.mkdir(parents=True, exist_ok=True)
    TASK_PATH.parent.mkdir(parents=True, exist_ok=True)
    PACKAGE_PATH.parent.mkdir(parents=True, exist_ok=True)
    TASK_PATH.write_text("".join(compact(task) + "\n" for task in tasks))
    PACKAGE_PATH.write_text(json.dumps({
        "schema_version": 1,
        "packages": [{"task_id": task["id"], **task["extension_diagnostic_package"]} for task in tasks],
    }, indent=2, sort_keys=True) + "\n")
    family_payload = {
        "schema_version": 1,
        "families": [{
            "family_id": family_id,
            "source_rows": len(rows),
            "mechanisms": sorted({row["mechanism"] for row in rows}),
            "subjects": sorted({row["subject"] for row in rows}),
            "behaviorally_real": any(row["behavior_decision"] == "behaviorally_real" for row in rows),
            "planner_representable": family_id in task_candidates,
            "selected_task_ids": [task["id"] for task in tasks if task["independent_family_id"] == family_id],
        } for family_id, rows in sorted(families.items())],
        "directed_comparison_pairs": pairs,
    }
    FAMILY_PATH.write_text(json.dumps(family_payload, indent=2, sort_keys=True) + "\n")
    CELL_PATH.write_text(json.dumps({
        "schema_version": 1, "cells": cells,
        "web_task_ids": [task["id"] for task in web_tasks],
        "web_corpus_grid_count": len(web_tasks) * len(cells),
    }, indent=2, sort_keys=True) + "\n")
    write_csv(OUT / "ext_v2_screen.csv", [screen_row(row) for row in audited])
    (OUT / "oracle_validation.json").write_text(json.dumps({
        "passed": len(validation), "total": len(validation), "checks": validation,
    }, indent=2, sort_keys=True) + "\n")

    summary = {
        "candidate_source_rows": len(audited),
        "unique_source_mechanisms": len({row["mechanism"] for row in audited}),
        "behaviorally_real_source_rows": sum(row["behavior_decision"] == "behaviorally_real" for row in audited),
        "behaviorally_real_mechanisms": len({row["mechanism"] for row in audited if row["behavior_decision"] == "behaviorally_real"}),
        "behaviorally_real_families": sum(any(row["behavior_decision"] == "behaviorally_real" for row in rows) for rows in families.values()),
        "planner_representable_families": len(task_candidates),
        "ext_v2_tasks": len(tasks),
        "web_eligible_tasks": len(web_tasks),
        "web_corpus_grids": len(web_tasks) * len(cells),
        "directed_cds_instances": len(pairs),
        "target_family_threshold_met": len({task["independent_family_id"] for task in tasks}) >= 12,
        "hashes": {str(path.relative_to(ROOT)): sha256(path) for path in (
            W5, MAIN, W6_TASKS, ROOT / "data/conflicts.jsonl",
            ROOT / "config/resource_model.json", ROOT / "harness/config/arena.json",
            ROOT / "prereg/ext_v2_cds_20261016.md", Path(__file__),
            TASK_PATH, PACKAGE_PATH, FAMILY_PATH, CELL_PATH,
            OUT / "ext_v2_screen.csv", OUT / "oracle_validation.json",
        )},
    }
    (OUT / "setup_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
