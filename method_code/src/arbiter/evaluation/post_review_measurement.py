"""Deterministic post-review measurement helpers over immutable logs."""
from __future__ import annotations

from typing import Any

from .external_scoring import ACTION_METHODS, _matches


TERMINAL_PRIORITY = (
    ("solved", "SR=1 and gold feasible"),
    ("correctly_infeasible", "SR=1 and gold infeasible"),
    ("budget_exhausted", "unsuccessful and primary cost >= 90"),
    ("execution_failed", "unsuccessful and any semantic action failed"),
    ("plan_not_found", "gold feasible, reported infeasible, no semantic action"),
    ("incorrectly_infeasible", "gold feasible, reported infeasible after semantic actions"),
    ("execution_failed", "remaining unsuccessful episode"),
)


def is_stale(vector: dict[str, bool | None] | None, world: str) -> bool:
    return bool(vector and vector.get(world) is False
                and any(value is True for value in vector.values()))


def pipeline_plan_source(
    record: dict[str, Any], vectors: dict[str, dict[str, bool | None]],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Use saved execution provenance: validation branch versus selected plan."""
    actions = [event for event in record["events"] if event.get("method") in ACTION_METHODS]
    mechanisms = list(record.get("used_mechanism_ids", []))
    if len(actions) != len(mechanisms):
        raise ValueError("pipeline action/mechanism alignment mismatch")
    details = []
    stale_all = stale_commit = 0
    for index, (event, mechanism) in enumerate(zip(actions, mechanisms)):
        role = "probe" if event.get("intent") == "probe" else "commit"
        stale = is_stale(vectors.get(mechanism), record["world"])
        stale_all += int(stale)
        stale_commit += int(stale and role == "commit")
        details.append({
            "action_index": index, "mechanism_id": mechanism,
            "method": event["method"], "plan_source_role": role,
            "source": "validation_routine" if role == "probe" else "selected_plan_step",
            "stale": int(stale), "success": event.get("success"),
            "primary_cost": event["cost"]["primary"],
        })
    result = {
        "HTR_all": int(bool(stale_all)),
        "HTR_commit": int(bool(stale_commit)),
        "probe_count": sum(event.get("intent") == "probe" for event in actions),
        "probe_cost": int(sum(float(event["cost"]["primary"]) for event in actions
                              if event.get("intent") == "probe")),
        # Retained solely to document the retired circular first-use rule.
        "degenerate_first_use_HTR_commit": 0,
    }
    accepted = record["metrics"]
    if result["HTR_all"] != int(accepted["HTR_all"]):
        raise ValueError("plan-source HTR_all differs from accepted metric")
    if result["HTR_commit"] != int(accepted["HTR_commit"]):
        raise ValueError("plan-source HTR_commit differs from accepted main metric")
    return result, details


def external_self_reported(
    record: dict[str, Any], events: list[dict[str, Any]],
    conflicts: tuple[dict[str, Any], ...], nodes: dict[str, str],
) -> tuple[dict[str, int], list[dict[str, Any]]]:
    """Audit whether a probe-labelled claim is later used by a commit action."""
    semantic = []
    for index, event in enumerate(events):
        if event.get("method") not in ACTION_METHODS:
            continue
        candidates = [row for row in conflicts if _matches(event, row["assertion"], nodes)]
        semantic.append((index, event, candidates))
    details = []
    violation_claims: set[str] = set()
    stale_violation_claims: set[str] = set()
    for position, (index, event, candidates) in enumerate(semantic):
        if event.get("intent") != "probe":
            continue
        candidate_ids = {str(row["id"]) for row in candidates}
        later = set()
        for _, later_event, later_candidates in semantic[position + 1:]:
            if later_event.get("intent") == "probe":
                continue
            later.update(candidate_ids & {str(row["id"]) for row in later_candidates})
        stale = {
            str(row["id"]) for row in candidates
            if is_stale(row["truth_vector"], record["world"])
        }
        violation_claims.update(later)
        stale_violation_claims.update(later & stale)
        details.append({
            "event_index": index, "method": event["method"],
            "candidate_claim_ids": sorted(candidate_ids),
            "stale_candidate_claim_ids": sorted(stale),
            "later_committed_claim_ids": sorted(later),
            "violation": int(bool(later)),
            "stale_violation": int(bool(later & stale)),
        })
    metrics = record["metrics"]
    return {
        "HTR_all": int(metrics["HTR_all"]),
        "HTR_commit": int(metrics.get("self_reported_HTR_commit", metrics["HTR_commit"])),
        "degenerate_first_use_HTR_commit": int(metrics["HTR_commit"]),
        "consistency_violation": int(bool(violation_claims)),
        "stale_consistency_violation": int(bool(stale_violation_claims)),
        "violation_claims": len(violation_claims),
        "stale_violation_claims": len(stale_violation_claims),
    }, details


def terminal(record: dict[str, Any]) -> str:
    if record["metrics"]["SR"]:
        return "solved" if record["true_feasible"] else "correctly_infeasible"
    semantic = [event for event in record["events"] if event.get("method") in ACTION_METHODS]
    if float(record["metrics"]["primary_cost"]) >= 90:
        return "budget_exhausted"
    if any(event.get("success") is False for event in semantic):
        return "execution_failed"
    if record["true_feasible"] and record["reported_feasible"] is False:
        return "plan_not_found" if not semantic else "incorrectly_infeasible"
    return "execution_failed"


def stable_identification_cost(events: list[dict[str, Any]], expected_hash: str) -> tuple[str, float | None]:
    reports = []
    cumulative = 0.0
    for event in events:
        cumulative += float(event["cost"]["primary"])
        if event.get("method") == "report_version":
            reports.append((event.get("params", {}).get("guess_sha256"), cumulative))
    if not reports:
        return "no_report", None
    if reports[-1][0] != expected_hash:
        return "never_stably_correct", None
    start = len(reports) - 1
    while start and reports[start - 1][0] == expected_hash:
        start -= 1
    return "identified", reports[start][1]
