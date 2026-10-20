#!/usr/bin/env python3
"""Audit whether expansion mechanisms are observable in the frozen arena."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import os
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from oracle.planner import (  # noqa: E402
    INITIAL,
    SOURCE_SPECS,
    VERSIONS,
    OraclePlanner,
    ingredient_slots,
    recipe_layout,
    validate_plan,
)
from sim.semantic_agent import create_simulated_agent  # noqa: E402


REPORT_DIR = ROOT / "reports" / "expansion_probe"
PREREG = ROOT / "prereg" / "expansion_probe.md"
MARKER = b"## Mechanism authenticity audit\n"
CONTROL = "drop:gold_ore"


def compact(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def rule_hash() -> str:
    content = PREREG.read_bytes()
    if MARKER not in content:
        raise RuntimeError("mechanism-authenticity preregistration section is missing")
    return sha256_bytes(content[content.index(MARKER):])


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def public_action(action: dict[str, Any]) -> dict[str, Any]:
    method = action["method"]
    if method == "mine":
        return {
            "method": method,
            "node_type": action["node_type"],
            "node_ordinal": action["node_ordinal"],
        }
    if method == "craft_grid":
        return {"method": method, "layout": action["layout"]}
    if method == "smelt":
        return {
            "method": method,
            "input": action["input"],
            "fuel": action["fuel"],
            "n": action["n"],
        }
    raise ValueError(f"unsupported action: {method}")


def normalized_terminal_result(response: dict[str, Any]) -> dict[str, Any]:
    success = response.get("success") is not False
    products = response.get("items")
    if products is None:
        product = response.get("product")
        products = [] if product is None else [product]
    products = sorted(
        ({"name": row["name"], "count": int(row["count"])} for row in products),
        key=lambda row: (row["name"], row["count"]),
    )
    error = response.get("error")
    if not success and error is None:
        error = "no_matching_recipe"
    return {
        "requirements_feasible": True,
        "success": success,
        "products": products,
        "error_category": error,
    }


def terminal_key(action: dict[str, Any]) -> str:
    return compact(public_action(action))


def recipe_actions(planners: dict[str, OraclePlanner], output: str) -> list[dict[str, Any]]:
    """Enumerate every concrete terminal action reachable in at least one world."""
    actions: dict[str, dict[str, Any]] = {}
    for version in VERSIONS:
        planner = planners[version]
        for recipe in planner.by_output.get(output, []):
            choices_by_slot = [
                sorted(option for option in slot if option in planner.reachable)
                for slot in recipe["slots"]
            ]
            if any(not choices for choices in choices_by_slot):
                continue
            for choices in itertools.product(*choices_by_slot):
                if recipe["station"] == "crafting_grid":
                    action = {
                        "method": "craft_grid",
                        "layout": recipe_layout(recipe["raw"], list(choices)),
                    }
                else:
                    action = {
                        "method": "smelt",
                        "input": choices[0],
                        "fuel": "coal",
                        "n": 1,
                    }
                actions[terminal_key(action)] = action
    return [actions[key] for key in sorted(actions)]


def requirements_for(action: dict[str, Any]) -> Counter[str]:
    if action["method"] == "craft_grid":
        return Counter(item for item in action["layout"] if item)
    if action["method"] == "smelt":
        return Counter({action["input"]: action["n"], action["fuel"]: 1})
    if action["method"] == "mine":
        tool = SOURCE_SPECS[action["node_type"]]["tool"]
        return Counter({tool: 1}) if tool else Counter()
    raise ValueError(action["method"])


def terminal_response(
    version: str,
    action: dict[str, Any],
    requirements: Counter[str],
    prerequisite: Any,
    arena: dict[str, Any],
) -> dict[str, Any]:
    if not prerequisite.feasible:
        return {
            "requirements_feasible": False,
            "success": False,
            "products": [],
            "error_category": "requirements_infeasible",
        }

    inventory = Counter(INITIAL)
    for item, count in requirements.items():
        inventory[item] = max(inventory[item], count)
    handle = create_simulated_agent(version=version, initial_inventory=inventory)
    api = handle["api"]
    try:
        if action["method"] == "craft_grid":
            response = api.craft_grid(action["layout"])
        elif action["method"] == "smelt":
            response = api.smelt(action["input"], action["fuel"], action["n"])
        elif action["method"] == "mine":
            nodes = [
                row for row in arena["resources"]
                if row["block"] == action["node_type"]
            ]
            response = api.mine(nodes[action["node_ordinal"] - 1]["nodeId"])
        else:
            raise ValueError(action["method"])
        return normalized_terminal_result(response)
    finally:
        handle["shutdown"]()


def action_cost(action: dict[str, Any]) -> int:
    return int(action.get("n", 1))


def evaluate_action(
    planners: dict[str, OraclePlanner],
    action: dict[str, Any],
    arena: dict[str, Any],
) -> dict[str, Any]:
    requirements = requirements_for(action)
    prerequisites = {}
    outcomes = {}
    for version in VERSIONS:
        if requirements:
            plan = planners[version].plan_requirements(
                dict(requirements), label=f"terminal:{terminal_key(action)}"
            )
        else:
            plan = planners[version].plan_requirements(
                {"stick": 1}, label=f"terminal:{terminal_key(action)}"
            )
            # The dummy requirement is already in R0 and is not part of the witness.
            plan.actions = []
            plan.primary_cost = 0
        if plan.feasible:
            validate_plan(plan)
        prerequisites[version] = plan
        outcomes[version] = terminal_response(version, action, requirements, plan, arena)

    observable_signatures = {compact(outcomes[version]) for version in VERSIONS}
    source_versions = [version for version in VERSIONS if prerequisites[version].feasible]
    source_version = min(
        source_versions,
        key=lambda version: (
            prerequisites[version].primary_cost,
            len(prerequisites[version].actions),
            version,
        ),
    )
    source_plan = prerequisites[source_version]
    witness = [public_action(row) for row in source_plan.actions] + [public_action(action)]
    return {
        "action": action,
        "requirements": dict(sorted(requirements.items())),
        "prerequisites": prerequisites,
        "outcomes": outcomes,
        "distinguishes": len(observable_signatures) >= 2,
        "source_version": source_version,
        "primary_cost": int(source_plan.primary_cost or 0) + action_cost(action),
        "witness": witness,
    }


def ingredient_members(assertion: dict[str, Any]) -> set[str]:
    if assertion.get("kind") == "concrete_grid":
        return {item for item in assertion.get("layout", []) if item}
    members = {
        item
        for row in assertion.get("grid", [])
        for cell in row
        for item in cell
    }
    members.update(
        item for slot in assertion.get("ingredients", []) for item in slot
    )
    return members


def representation_source(
    assertions: list[dict[str, Any]], planners: dict[str, OraclePlanner]
) -> tuple[str, str]:
    member_sets = [ingredient_members(row) for row in assertions]
    changed = set()
    for left, right in itertools.combinations(member_sets, 2):
        changed.update(left ^ right)
    reachable = set().union(*(planner.reachable for planner in planners.values()))
    if changed and not (changed & reachable):
        return "tag_members_outside_arena", ";".join(sorted(changed))
    if changed:
        return "equivalent_alternative_ingredient", ";".join(sorted(changed))
    normalized = {compact(row) for row in assertions}
    if len(normalized) > 1:
        return "recipe_json_format_change", "normalized assertions differ without ingredient-member drift"
    return "other_representational", "no executable distinguishing witness"


def conflict_rows_for(mechanism: str, conflicts: list[dict[str, Any]]) -> list[dict[str, Any]]:
    kind, subject = mechanism.split(":", 1)
    if kind == "recipe":
        return [
            row for row in conflicts
            if row["assertion"].get("kind") in {"recipe", "concrete_grid"}
            and row["assertion"].get("output") == subject
        ]
    return [
        row for row in conflicts
        if row["assertion"].get("kind") == "drop"
        and row["assertion"].get("block") == subject
    ]


def candidate_rows() -> list[dict[str, str]]:
    rows = read_csv(REPORT_DIR / "targets_934.csv")
    return [
        row for row in rows
        if row["primary_type"] != "no_conflict"
        and row["core_target"] == "0"
        and row["shares_core_dominant"] == "0"
    ]


def split_values(value: str) -> set[str]:
    return {item for item in value.split(";") if item}


def audit() -> tuple[list[dict[str, Any]], dict[str, Any]]:
    if os.environ.get("PYTHONHASHSEED") != "0":
        raise SystemExit("accepted audit runs require PYTHONHASHSEED=0")

    families = read_csv(REPORT_DIR / "mechanism_families.csv")
    mechanisms = sorted(
        row["mechanisms"] for row in families if row["new_after_dedup"] == "1"
    )
    if len(mechanisms) != 32 or any(";" in value for value in mechanisms):
        raise RuntimeError(f"expected 32 singleton new mechanisms, got {len(mechanisms)}")

    conflicts = [
        json.loads(line)
        for line in (ROOT / "data" / "conflicts.jsonl").read_text().splitlines()
        if line
    ]
    arena = json.loads((ROOT / "harness" / "config" / "arena.json").read_text())
    capacities = json.loads((ROOT / "config" / "resource_model.json").read_text())[
        "resource_block_counts"
    ]
    planners = {
        version: OraclePlanner(version, capacities=capacities) for version in VERSIONS
    }
    candidates = candidate_rows()
    closed = read_csv(REPORT_DIR / "corpus_closed_candidates.csv")
    frozen_rule_hash = rule_hash()
    output = []

    for mechanism in mechanisms + [CONTROL]:
        scope = "new_after_dedup" if mechanism != CONTROL else "diagnostic_control"
        exact_rows = conflict_rows_for(mechanism, conflicts)
        if not exact_rows:
            raise RuntimeError(f"no conflict rows for {mechanism}")
        kind, subject = mechanism.split(":", 1)
        if kind == "recipe":
            actions = recipe_actions(planners, subject)
        else:
            actions = [{"method": "mine", "node_type": subject, "node_ordinal": 1}]
        if not actions:
            raise RuntimeError(f"no reachable concrete action for {mechanism}")

        evaluations = [evaluate_action(planners, action, arena) for action in actions]
        distinguishing = [row for row in evaluations if row["distinguishes"]]
        ranked = distinguishing or [
            row for row in evaluations
            if sum(outcome["requirements_feasible"] for outcome in row["outcomes"].values()) >= 2
        ]
        if not ranked:
            ranked = evaluations
        selected = min(
            ranked,
            key=lambda row: (
                row["primary_cost"],
                len(row["witness"]),
                row["source_version"],
                terminal_key(row["action"]),
            ),
        )
        behavioral = bool(distinguishing)
        if behavioral:
            rep_source, rep_detail = "", ""
            evidence_kind = "shortest_distinguishing_witness"
        else:
            rep_source, rep_detail = representation_source(
                [row["assertion"] for row in exact_rows], planners
            )
            evidence_kind = "exhaustive_no_distinguishing_witness"

        mechanism_candidates = sorted(
            row["target"] for row in candidates
            if mechanism in split_values(row["path_mechanisms"])
        )
        mechanism_closed = sorted(
            row["target"] for row in closed
            if mechanism in split_values(row["path_mechanisms"])
        )
        prereq_summary = {
            version: {
                "feasible": plan.feasible,
                "primary_cost": plan.primary_cost,
                "actions": [public_action(row) for row in plan.actions],
            }
            for version, plan in selected["prerequisites"].items()
        }
        distinct_versions = [
            f"{left}!={right}"
            for left, right in itertools.combinations(VERSIONS, 2)
            if selected["outcomes"][left] != selected["outcomes"][right]
        ]
        output.append({
            "mechanism": mechanism,
            "audit_scope": scope,
            "decision": "behaviorally_real" if behavioral else "representational_difference",
            "representation_source": rep_source,
            "representation_detail": rep_detail,
            "evidence_kind": evidence_kind,
            "concrete_actions_checked": len(evaluations),
            "exact_conflict_ids": ";".join(sorted(row["id"] for row in exact_rows)),
            "candidate_count": len(mechanism_candidates),
            "candidate_tasks": ";".join(mechanism_candidates),
            "corpus_closed_candidate_count": len(mechanism_closed),
            "corpus_closed_candidate_tasks": ";".join(mechanism_closed),
            "witness_source_version": selected["source_version"] if behavioral else "",
            "witness_comparison_versions": ";".join(distinct_versions) if behavioral else "",
            "witness_primary_cost": selected["primary_cost"] if behavioral else "",
            "terminal_action_json": compact(public_action(selected["action"])),
            "witness_actions_json": compact(selected["witness"]) if behavioral else "[]",
            "prerequisite_proofs_json": compact(prereq_summary),
            "outcome_1.16.5_json": compact(selected["outcomes"]["1.16.5"]),
            "outcome_1.19_json": compact(selected["outcomes"]["1.19"]),
            "outcome_1.21.11_json": compact(selected["outcomes"]["1.21.11"]),
            "rule_sha256": frozen_rule_hash,
        })

    behavioral = {
        row["mechanism"] for row in output
        if row["audit_scope"] == "new_after_dedup"
        and row["decision"] == "behaviorally_real"
    }
    eligible_closed = [
        row for row in closed
        if split_values(row["cluster_id"]) & behavioral
    ]
    closed_by_cluster: dict[str, list[str]] = defaultdict(list)
    for row in eligible_closed:
        closed_by_cluster[row["cluster_id"]].append(row["target"])
    cap_terms = {
        cluster: {
            "candidate_count": len(targets),
            "capped_at_four": min(4, len(targets)),
        }
        for cluster, targets in sorted(closed_by_cluster.items())
    }
    upper_bound = sum(row["capped_at_four"] for row in cap_terms.values())
    summary = {
        "rule_sha256": frozen_rule_hash,
        "new_mechanisms_audited": len(mechanisms),
        "diagnostic_controls": 1,
        "behaviorally_real_new_mechanism_clusters": len(behavioral),
        "behaviorally_real_new_mechanisms": sorted(behavioral),
        "representational_new_mechanism_clusters": len(mechanisms) - len(behavioral),
        "corpus_closed_task_upper_bound_max_four_per_cluster": upper_bound,
        "upper_bound_cluster_terms": cap_terms,
        "inputs": {
            str(path.relative_to(ROOT)): sha256_bytes(path.read_bytes())
            for path in (
                PREREG,
                REPORT_DIR / "mechanism_families.csv",
                REPORT_DIR / "targets_934.csv",
                REPORT_DIR / "corpus_closed_candidates.csv",
                ROOT / "data" / "conflicts.jsonl",
                ROOT / "config" / "resource_model.json",
                ROOT / "config" / "recipe_type_policy.json",
                ROOT / "harness" / "config" / "arena.json",
                ROOT / "sim" / "semantic_agent.py",
                *(ROOT / "truth" / f"{version}.json" for version in VERSIONS),
            )
        },
    }
    return output, summary


def render_report(rows: list[dict[str, Any]], summary: dict[str, Any]) -> str:
    focus = {
        "recipe:crafting_table", "recipe:furnace", "recipe:chest", "recipe:stick",
        "recipe:stone_axe", "recipe:stone_hoe", "recipe:stone_pickaxe",
        "recipe:stone_shovel", "recipe:stone_sword", "recipe:wooden_axe",
        "recipe:wooden_hoe", "recipe:wooden_shovel", "recipe:wooden_sword",
        "recipe:gold_ingot", "drop:gold_ore", "recipe:raw_gold_block",
        "recipe:raw_iron_block",
    }
    lines = [
        "# Expansion Mechanism Authenticity Audit",
        "",
        "This deterministic audit follows the rule frozen in `prereg/expansion_probe.md`. "
        "It ran no model and changed no frozen input.",
        "",
        "## Decision numbers",
        "",
        f"- Behaviorally real new mechanism clusters: "
        f"**{summary['behaviorally_real_new_mechanism_clusters']}**.",
        f"- Corpus-closed task upper bound at most four uses per cluster: "
        f"**{summary['corpus_closed_task_upper_bound_max_four_per_cluster']}**.",
        f"- Representational new mechanism clusters: "
        f"{summary['representational_new_mechanism_clusters']}.",
        f"- Decision-rule SHA-256: `{summary['rule_sha256']}`.",
        "",
        "The `drop:gold_ore` row is a diagnostic control and is excluded from both new-mechanism counts.",
        "",
        "## Required focus mechanisms",
        "",
        "| Mechanism | Scope | Decision | Representation source | Candidate tasks |",
        "|---|---|---|---|---:|",
    ]
    for row in rows:
        if row["mechanism"] in focus:
            lines.append(
                f"| {row['mechanism']} | {row['audit_scope']} | {row['decision']} | "
                f"{row['representation_source'] or '-'} | {row['candidate_count']} |"
            )
    lines.extend([
        "",
        "## All audited mechanisms",
        "",
        "| Mechanism | Decision | Source | Concrete actions checked | Corpus-closed candidates |",
        "|---|---|---|---:|---:|",
    ])
    for row in rows:
        lines.append(
            f"| {row['mechanism']} | {row['decision']} | "
            f"{row['representation_source'] or '-'} | {row['concrete_actions_checked']} | "
            f"{row['corpus_closed_candidate_count']} |"
        )
    lines.extend(["", "## Upper-bound arithmetic", ""])
    for cluster, values in summary["upper_bound_cluster_terms"].items():
        lines.append(
            f"- `{cluster}`: min(4, {values['candidate_count']}) = "
            f"{values['capped_at_four']}."
        )
    return "\n".join(lines) + "\n"


def main() -> None:
    first_rows, first_summary = audit()
    second_rows, second_summary = audit()
    first_payload = compact({"rows": first_rows, "summary": first_summary})
    second_payload = compact({"rows": second_rows, "summary": second_summary})
    if first_payload != second_payload:
        raise RuntimeError("two complete in-process audit runs differ")

    REPORT_DIR.mkdir(parents=True, exist_ok=True)
    fields = list(first_rows[0])
    write_csv(REPORT_DIR / "mechanism_authenticity.csv", first_rows, fields)
    (REPORT_DIR / "mechanism_authenticity_summary.json").write_text(
        json.dumps(first_summary, indent=2, sort_keys=True) + "\n"
    )
    (REPORT_DIR / "mechanism_authenticity_rule.sha256").write_text(
        f"{first_summary['rule_sha256']}  prereg/expansion_probe.md#mechanism-authenticity-audit\n"
    )
    (REPORT_DIR / "MECHANISM_AUTHENTICITY.md").write_text(
        render_report(first_rows, first_summary)
    )
    print(json.dumps({
        "behaviorally_real_new_mechanism_clusters": first_summary[
            "behaviorally_real_new_mechanism_clusters"
        ],
        "corpus_closed_task_upper_bound_max_four_per_cluster": first_summary[
            "corpus_closed_task_upper_bound_max_four_per_cluster"
        ],
        "result_sha256": sha256_bytes(first_payload.encode()),
        "deterministic_two_runs": True,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
