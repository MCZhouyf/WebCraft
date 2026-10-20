"""Gold-score the alias-locked replication and export its frozen comparisons."""
from __future__ import annotations

import csv
import hashlib
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from arbiter.evaluation.external_rescore import action_audit, outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
ARTIFACTS = ROOT / "artifacts/post_review/alias_locked_replication"
OUT = ROOT / "reports/main/post_review/alias_locked_replication"
RESCORE = ROOT / "reports/main/post_review/external_agents/rescore_v2/episode_metrics.csv"
STRATA = ROOT / "reports/main/post_review/alias_stratified/episode_strata.csv"
MODEL = "glm-5.2"
METHODS = ("react", "reflexion")
EXPECTED = {"react": 47, "reflexion": 10}
BOOTSTRAP_DRAWS = 10_000
PRIMARY = {
    "react": ("SR", None, 20261011),
    "reflexion": ("feasible_success", "true_feasible", 20261012),
}


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"empty output: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def row_key(row: dict) -> tuple[str, str, str, str, int]:
    return row["method"], row["task_id"], row["condition"], row["world"], int(row["seed"])


def ratio(rows: list[dict], numerator: str, denominator: str | None) -> float:
    num = sum(int(row[numerator]) for row in rows)
    den = sum(int(row[denominator]) if denominator else 1 for row in rows)
    return num / den if den else float("nan")


def bootstrap_ratio(rows: list[dict], numerator: str, denominator: str | None,
                    seed: int) -> tuple[float, float, float]:
    num = np.asarray([int(row[numerator]) for row in rows], dtype=float)
    den = np.asarray([int(row[denominator]) if denominator else 1 for row in rows], dtype=float)
    estimate = float(num.sum() / den.sum())
    indices = np.random.default_rng(seed).integers(0, len(rows), (BOOTSTRAP_DRAWS, len(rows)))
    sampled_den = den[indices].sum(axis=1)
    valid = sampled_den > 0
    samples = num[indices].sum(axis=1)[valid] / sampled_den[valid]
    return estimate, float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def aggregate(method: str, group: str, rows: list[dict]) -> dict:
    feasible = sum(int(row["true_feasible"]) for row in rows)
    _, metric_denominator, seed = PRIMARY[method]
    primary_name = "SR" if metric_denominator is None else "feasible_task_SR"
    primary_num = "SR" if metric_denominator is None else "feasible_success"
    primary, low, high = bootstrap_ratio(rows, primary_num, metric_denominator, seed)
    return {
        "method": method,
        "group": group,
        "n": len(rows),
        "feasible_n": feasible,
        "successes": sum(int(row["SR"]) for row in rows),
        "SR": ratio(rows, "SR", None),
        "feasible_successes": sum(int(row["feasible_success"]) for row in rows),
        "feasible_task_SR": ratio(rows, "feasible_success", "true_feasible"),
        "HTR_all": ratio(rows, "legacy_HTR_all", None),
        "self_reported_HTR_commit": ratio(rows, "legacy_self_reported_HTR_commit", None),
        "overconservatism": ratio(rows, "false_abstention", "true_feasible"),
        "comparison_metric": primary_name,
        "comparison_value": primary,
        "comparison_ci95_low": low,
        "comparison_ci95_high": high,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "bootstrap_seed": seed,
    }


def main() -> None:
    status = json.loads((ARTIFACTS / "status.json").read_text())
    if status["status"] != "completed" or status["episodes_completed"] != 57:
        raise RuntimeError("alias-locked replication is incomplete")
    raw_rows = jsonl(ARTIFACTS / "episodes.jsonl")
    if ({method: sum(row["method"] == method for row in raw_rows) for method in METHODS}
            != EXPECTED):
        raise RuntimeError("alias-locked method counts changed")
    if any(row["server_model_identifiers"] != [MODEL] for row in raw_rows):
        raise RuntimeError("locked replication contains another model identifier")

    tasks = {row["id"]: row for row in jsonl(WEBCRAFT / "data/tasks.jsonl")}
    conflicts = load_conflicts(WEBCRAFT / "data/conflicts.jsonl")
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    scored = []
    for row in raw_rows:
        events = jsonl(ROOT / row["action_log"])
        task = tasks[row["task_id"]]
        true_feasible = bool(task["feasible"][row["world"]])
        legacy = score_external_trace(
            events, world=row["world"], conflicts=conflicts, resource_nodes=nodes,
            true_feasible=true_feasible, reported_feasible=row["reported_feasible"],
        )
        target = task["target"]
        attained = sum(
            int(item["count"]) for item in row["final_inventory"]
            if item["name"].removeprefix("minecraft:") == target["item"]
        ) >= target["count"]
        success = int(
            (true_feasible and attained and row["reported_feasible"] is True)
            or (not true_feasible and row["reported_feasible"] is False)
        )
        working = {
            **row,
            "true_feasible": true_feasible,
            "metrics": {"SR": success, **legacy},
            "_max_calls": 40,
            "_max_actions": 90,
        }
        result = outcome(working, events, task)
        audited, _ = action_audit(working, events, conflicts, nodes)
        scored.append({
            "method": row["method"], "task_id": row["task_id"],
            "condition": row["condition"], "world": row["world"], "seed": row["seed"],
            "server_model_identifier": MODEL,
            **result, **audited,
            "model_calls": row["model_calls"], "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
        })

    first = []
    with RESCORE.open() as handle:
        first = [row for row in csv.DictReader(handle)
                 if row["source"] == "external" and row["method"] in METHODS]
    strata = list(csv.DictReader(STRATA.open()))
    majority_keys = {
        row_key(row) for row in strata
        if row["method"] in METHODS and row["server_model_identifier"] == MODEL
    }
    first_by_method: dict[str, list[dict]] = defaultdict(list)
    majority_by_method: dict[str, list[dict]] = defaultdict(list)
    locked_by_method: dict[str, list[dict]] = defaultdict(list)
    for row in first:
        first_by_method[row["method"]].append(row)
        if row_key(row) in majority_keys:
            majority_by_method[row["method"]].append(row)
    for row in scored:
        locked_by_method[row["method"]].append(row)
    if {method: len(majority_by_method[method]) for method in METHODS} != {"react": 63, "reflexion": 100}:
        raise RuntimeError("first-round majority strata changed")

    table = []
    for method in METHODS:
        table.extend((
            aggregate(method, "first_round_all_identifiers", first_by_method[method]),
            aggregate(method, "first_round_majority_identifier", majority_by_method[method]),
            aggregate(method, "locked_replication", locked_by_method[method]),
        ))

    judgments = []
    for method in METHODS:
        majority = next(row for row in table
                        if row["method"] == method and row["group"] == "first_round_majority_identifier")
        locked = next(row for row in table
                      if row["method"] == method and row["group"] == "locked_replication")
        within = majority["comparison_ci95_low"] <= locked["comparison_value"] <= majority["comparison_ci95_high"]
        judgments.append({
            "method": method,
            "comparison_metric": locked["comparison_metric"],
            "locked_value": locked["comparison_value"],
            "majority_ci95_low": majority["comparison_ci95_low"],
            "majority_ci95_high": majority["comparison_ci95_high"],
            "locked_value_within_majority_ci": within,
        })

    family = []
    for method in METHODS:
        majority = next(row for row in table
                        if row["method"] == method and row["group"] == "first_round_majority_identifier")
        locked = next(row for row in table
                      if row["method"] == method and row["group"] == "locked_replication")
        majority_feasible = majority["feasible_task_SR"] <= 0.10
        locked_feasible = locked["feasible_task_SR"] <= 0.10
        majority_htr = majority["HTR_all"] > 0.155
        locked_htr = locked["HTR_all"] > 0.155
        family.append({
            "method": method,
            "majority_feasible_SR_le_0_10": majority_feasible,
            "locked_feasible_SR_le_0_10": locked_feasible,
            "feasible_conclusion_unchanged": majority_feasible == locked_feasible,
            "majority_HTR_all_gt_0_155": majority_htr,
            "locked_HTR_all_gt_0_155": locked_htr,
            "HTR_conclusion_unchanged": majority_htr == locked_htr,
        })

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "episode_metrics.csv", scored)
    write_csv(OUT / "main_table.csv", table)
    write_csv(OUT / "judgments.csv", judgments)
    write_csv(OUT / "family_conclusions.csv", family)
    react = next(row for row in judgments if row["method"] == "react")
    reflexion = next(row for row in judgments if row["method"] == "reflexion")
    model_ids = sorted({row["server_model_identifier"] for row in scored})
    report = [
        "# Alias-locked subset replication",
        "",
        f"Actual server model identifier(s): {', '.join(model_ids)}.",
        f"ReAct judgment: locked overall SR {react['locked_value']:.4f} "
        f"{'falls' if react['locked_value_within_majority_ci'] else 'does not fall'} within the "
        f"first-round majority-layer bootstrap 95% CI "
        f"[{react['majority_ci95_low']:.4f}, {react['majority_ci95_high']:.4f}].",
        f"Reflexion judgment: locked feasible-task SR {reflexion['locked_value']:.4f} "
        f"{'falls' if reflexion['locked_value_within_majority_ci'] else 'does not fall'} within the "
        f"first-round majority-layer bootstrap 95% CI "
        f"[{reflexion['majority_ci95_low']:.4f}, {reflexion['majority_ci95_high']:.4f}].",
        "",
        "Family-threshold conclusions are reported without changing either threshold in "
        "`family_conclusions.csv`.",
        "The locked replication is an independent table and does not replace any first-round number.",
        "",
        f"Successful provider responses including canaries: {status['successful_provider_responses']}; "
        f"total HTTP attempts: {status['total_http_attempts']}; input/output tokens: "
        f"{status['input_tokens']}/{status['output_tokens']}.",
        "Actual provider cost: unavailable (not recorded as zero)." if status.get("actual_provider_cost") is None
        else f"Actual provider cost: {status['actual_provider_cost']}.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    provenance = {
        "model_identifier": MODEL,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "primary_bootstrap_seeds": {method: PRIMARY[method][2] for method in METHODS},
        "input_sha256": {
            str(path.relative_to(ROOT)): sha256(path)
            for path in (ARTIFACTS / "episodes.jsonl", ARTIFACTS / "status.json", RESCORE, STRATA)
        },
        "first_round_numbers_replaced": False,
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "model_identifiers": model_ids,
        "judgments": judgments,
        "family_conclusions": family,
        "successful_provider_responses": status["successful_provider_responses"],
        "total_http_attempts": status["total_http_attempts"],
        "actual_provider_cost": status.get("actual_provider_cost"),
    }, indent=2))


if __name__ == "__main__":
    main()
