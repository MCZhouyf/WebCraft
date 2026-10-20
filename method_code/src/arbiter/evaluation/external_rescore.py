"""Offline measurements; no agent, simulator, network or model dependencies."""
from __future__ import annotations

from typing import Any

import numpy as np

from .external_scoring import ACTION_METHODS, _matches


TERMINALS = (
    "solved", "correct_infeasible", "false_infeasible", "plan_not_found",
    "execution_failed", "budget_exhausted",
)


def expected_charge(event: dict[str, Any]) -> int:
    method = event["method"]
    if method == "smelt":
        n = event.get("params", {}).get("n")
        return n if type(n) is int and n > 0 else 1
    if method in ACTION_METHODS:
        return 1
    if method in {"search", "inventory", "submit", "report_version"}:
        return 0
    raise ValueError(f"unknown public method: {method}")


def outcome(row: dict[str, Any], events: list[dict[str, Any]],
            task: dict[str, Any]) -> dict[str, Any]:
    feasible = task["feasible"][row["world"]]
    if type(feasible) is not bool or row["true_feasible"] is not feasible:
        raise ValueError("gold feasibility disagreement")
    inventories = [e for e in events if e["method"] == "inventory"]
    submissions = [e for e in events if e["method"] == "submit"]
    if not inventories or not submissions or events[-1]["method"] != "inventory":
        raise ValueError("missing terminal inventory/submission")
    reported = submissions[-1]["params"]["result"]["feasible"]
    if reported is not row["reported_feasible"]:
        raise ValueError("submission disagreement")
    target = task["target"]
    quantity = sum(int(i["count"]) for i in inventories[-1]["result"]
                   if i["name"].removeprefix("minecraft:") == target["item"])
    attained = quantity >= target["count"]
    success = bool((feasible and attained and reported is True)
                   or (not feasible and reported is False))
    semantic = [e for e in events if e["method"] in ACTION_METHODS]
    cost = sum(float(e["cost"]["primary"]) for e in events)
    call_limit = row.get("_max_calls", 40)
    action_limit = row.get("_max_actions", 90)
    call_hit = bool(row.get("model_calls", 0) >= call_limit)
    action_hit = cost >= action_limit or any(
        d.get("error_code") == "action_budget_exceeded" for d in row.get("diagnostics", [])
    )
    if feasible and success:
        terminal, basis = "solved", "goal_and_affirmative_submission"
    elif not feasible and reported is False:
        terminal, basis = "correct_infeasible", "gold_and_submission"
    elif feasible and reported is False:
        terminal, basis = "false_infeasible", "gold_and_submission"
    elif call_hit or action_hit:
        terminal, basis = "budget_exhausted", "cap_reached_without_success_or_abstention"
    elif semantic and semantic[-1].get("success") is False:
        terminal, basis = "execution_failed", "derived_last_semantic_action_failed"
    else:
        terminal, basis = "plan_not_found", "derived_unsolved_without_terminal_failure_or_cap"
    failed = sum(e.get("success") is False for e in semantic)
    return {
        "true_feasible": int(feasible), "reported_feasible": reported,
        "SR": int(success), "original_SR": int(row["metrics"]["SR"]),
        "goal_attained": int(attained), "feasible_success": int(feasible and success),
        "correct_infeasible": int(not feasible and reported is False),
        "false_abstention": int(feasible and reported is False),
        "terminal": terminal, "terminal_basis": basis,
        "terminal_execution_failure": int(terminal == "execution_failed"),
        "any_action_failure": int(failed > 0), "failed_actions": failed,
        "semantic_actions": len(semantic), "primary_cost": cost,
        "call_cap_hit": int(call_hit), "action_cap_hit": int(action_hit),
    }


def action_audit(row: dict[str, Any], events: list[dict[str, Any]],
                 conflicts: tuple[dict[str, Any], ...], nodes: dict[str, str]
                 ) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    vectors = {c["id"]: c["truth_vector"] for c in conflicts}
    mechanisms = row.get("used_mechanism_ids")
    semantic = [e for e in events if e["method"] in ACTION_METHODS]
    if mechanisms is not None and len(mechanisms) != len(semantic):
        raise ValueError("mechanism/action alignment mismatch")
    audit = []
    semantic_index = 0
    for index, event in enumerate(events):
        if event["method"] not in ACTION_METHODS:
            continue
        candidates = [c for c in conflicts if _matches(event, c["assertion"], nodes)]
        stale = [c["id"] for c in candidates if c["truth_vector"].get(row["world"]) is False
                 and any(v is True for v in c["truth_vector"].values())]
        mechanism = mechanisms[semantic_index] if mechanisms is not None else None
        vector = vectors.get(mechanism, {})
        selected_stale = (vector.get(row["world"]) is False
                          and any(v is True for v in vector.values()))
        contact = bool(stale) if mechanism is None else selected_stale
        audit.append({
            "event_index": index, "semantic_index": semantic_index,
            "method": event["method"], "params": event.get("params", {}),
            "logged_intent": event.get("intent", "commit"),
            "success": event.get("success"), "primary_cost": event["cost"]["primary"],
            "candidate_ids": [c["id"] for c in candidates], "stale_candidate_ids": stale,
            "selected_mechanism_id": mechanism,
            "selected_mechanism_stale": selected_stale if mechanism is not None else None,
            "contact_upper": int(contact),
            "claim_identity": "recorded_operator" if mechanism is not None else "action_match_only",
            "behavioral_role": "unidentified" if contact else "not_stale_in_catalogue",
            "behavioral_commit_lower": 0, "behavioral_commit_upper": int(contact),
            "reason": "no_independent_plan_dependency_witness" if contact else "no_stale_contact",
        })
        semantic_index += 1
    upper = int(any(a["contact_upper"] for a in audit))
    intent_contact = int(any(a["contact_upper"] and a["logged_intent"] != "probe" for a in audit))
    probes = [e for e in events if e.get("intent") == "probe"]
    world_probes = [e for e in probes if e["method"] in ACTION_METHODS]
    return {
        "legacy_HTR_all": int(row["metrics"]["HTR_all"]),
        "legacy_self_reported_HTR_commit": int(row["metrics"].get(
            "self_reported_HTR_commit", row["metrics"]["HTR_commit"])),
        "catalogue_contact_upper": upper,
        "intent_contact_proxy": intent_contact,
        "behavioral_HTR_commit": None if upper else 0,
        "behavioral_HTR_commit_lower": 0, "behavioral_HTR_commit_upper": upper,
        "behavioral_unknown": upper,
        "labelled_probe_count": len(probes), "world_probe_count": len(world_probes),
        "free_labelled_probe_count": len(probes) - len(world_probes),
        "probe_cost": sum(float(e["cost"]["primary"]) for e in probes),
        "world_probe_cost": sum(float(e["cost"]["primary"]) for e in world_probes),
        "cost_mismatches": sum(float(e["cost"]["primary"]) != expected_charge(e) for e in events),
        "search_count": sum(e["method"] == "search" for e in events),
    }, audit


def clustered_ratio(rows: list[dict[str, Any]], numerator: str,
                    denominator: str | None = None, *, draws: int = 10000,
                    seed: int = 20261005) -> dict[str, Any]:
    tasks = sorted({r["task_id"] for r in rows})
    num = np.array([sum(float(r[numerator]) for r in rows if r["task_id"] == t) for t in tasks])
    den = np.array([sum(float(r[denominator]) if denominator else 1 for r in rows
                        if r["task_id"] == t) for t in tasks])
    if not tasks:
        raise ValueError("empty bootstrap")
    sample = np.random.default_rng(seed).integers(0, len(tasks), (draws, len(tasks)))
    sampled_den = den[sample].sum(axis=1)
    good = sampled_den > 0
    values = num[sample].sum(axis=1)[good] / sampled_den[good]
    return {
        "estimate": float(num.sum() / den.sum()) if den.sum() else None,
        "low": float(np.quantile(values, .025)) if len(values) else None,
        "high": float(np.quantile(values, .975)) if len(values) else None,
        "clusters": len(tasks), "draws": draws,
        "zero_denominator_draws": int((~good).sum()), "seed": seed,
    }


def paired_difference(left: list[dict[str, Any]], right: list[dict[str, Any]],
                      metric: str, *, within_world: bool = False,
                      feasible_only: bool = False) -> dict[str, Any]:
    keys = ("task_id", "world") if within_world else ("task_id", "world", "condition")
    def index(rows):
        result = {}
        for r in rows:
            key = tuple(r[k] for k in keys)
            if key in result:
                raise ValueError("duplicate paired key")
            result[key] = r
        return result
    a, b = index(left), index(right)
    if a.keys() != b.keys():
        raise ValueError("unpaired comparison")
    differences = []
    for key in sorted(a):
        if a[key]["true_feasible"] != b[key]["true_feasible"]:
            raise ValueError("paired feasibility mismatch")
        eligible = a[key]["true_feasible"] if feasible_only else 1
        differences.append({"task_id": a[key]["task_id"], "eligible": eligible,
                            "difference": eligible * (a[key][metric] - b[key][metric])})
    return clustered_ratio(differences, "difference", "eligible")
