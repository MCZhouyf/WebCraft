"""Gold-score the completed second-model probe offline and export its gate."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from pathlib import Path

import numpy as np

from arbiter.evaluation.external_rescore import action_audit, outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
ARTIFACTS = ROOT / "artifacts/post_review/external_second_model_probe"
OUT = ROOT / "reports/main/post_review/external_second_model_probe"
RESCORE = ROOT / "reports/main/post_review/external_agents/rescore_v2/episode_metrics.csv"
PIPELINE = {"naive_rag", "majority_rag", "truthfinder_rag", "verify_all", "arbiter"}
EXTERNAL = {"direct_llm", "react", "reflexion", "adam", "xenon"}
DRAW = 10_000
SEED = 20261011


def jsonl(path: Path) -> list[dict]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def write_csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        raise ValueError(f"empty output: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def interval(values: list[int], seed: int = SEED) -> tuple[float, float, float]:
    data = np.asarray(values, dtype=float)
    estimate = float(data.mean())
    indices = np.random.default_rng(seed).integers(0, len(data), (DRAW, len(data)))
    samples = data[indices].mean(axis=1)
    return estimate, float(np.quantile(samples, 0.025)), float(np.quantile(samples, 0.975))


def aggregate(method: str, rows: list[dict], source: str, scope: str) -> dict:
    feasible = [row for row in rows if int(row["true_feasible"])]
    sr, sr_low, sr_high = interval([int(row["SR"]) for row in rows])
    feasible_sr, feasible_low, feasible_high = interval([int(row["feasible_success"]) for row in feasible])
    htr, htr_low, htr_high = interval([int(row["legacy_HTR_all"]) for row in rows])
    commit, commit_low, commit_high = interval(
        [int(row["legacy_self_reported_HTR_commit"]) for row in rows]
    )
    over = sum(int(row["false_abstention"]) for row in feasible) / len(feasible)
    return {
        "method": method,
        "source": source,
        "scope": scope,
        "n": len(rows),
        "feasible_n": len(feasible),
        "successes": sum(int(row["SR"]) for row in rows),
        "SR": sr, "SR_ci95_low": sr_low, "SR_ci95_high": sr_high,
        "feasible_successes": sum(int(row["feasible_success"]) for row in feasible),
        "feasible_task_SR": feasible_sr,
        "feasible_SR_ci95_low": feasible_low, "feasible_SR_ci95_high": feasible_high,
        "HTR_all": htr, "HTR_all_ci95_low": htr_low, "HTR_all_ci95_high": htr_high,
        "self_reported_HTR_commit": commit,
        "self_reported_commit_ci95_low": commit_low,
        "self_reported_commit_ci95_high": commit_high,
        "overconservatism": over,
    }


def main() -> None:
    status = json.loads((ARTIFACTS / "status.json").read_text())
    if status["status"] != "completed" or status["episodes_completed"] != 150:
        raise RuntimeError("second-model probe is incomplete")
    tasks = {row["id"]: row for row in jsonl(WEBCRAFT / "data/tasks.jsonl")}
    conflicts = load_conflicts(WEBCRAFT / "data/conflicts.jsonl")
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    scored = []
    for row in jsonl(ARTIFACTS / "episodes.jsonl"):
        events = jsonl(ROOT / row["action_log"])
        task = tasks[row["task_id"]]
        true_feasible = bool(task["feasible"][row["world"]])
        legacy = score_external_trace(
            events, world=row["world"], conflicts=conflicts, resource_nodes=nodes,
            true_feasible=true_feasible, reported_feasible=row["reported_feasible"],
        )
        working = {
            **row,
            "true_feasible": true_feasible,
            "metrics": {"SR": 0, **legacy},
            "_max_calls": 40,
            "_max_actions": 90,
        }
        # Gold outcome recomputes SR from terminal inventory/submission. Seed the
        # legacy field only so outcome can assert equality after recomputation.
        target = task["target"]
        final = [event for event in events if event["method"] == "inventory"][-1]["result"]
        attained = sum(int(item["count"]) for item in final
                       if item["name"].removeprefix("minecraft:") == target["item"]) >= target["count"]
        working["metrics"]["SR"] = int(
            (true_feasible and attained and row["reported_feasible"] is True)
            or (not true_feasible and row["reported_feasible"] is False)
        )
        result = outcome(working, events, task)
        audited, _ = action_audit(working, events, conflicts, nodes)
        scored.append({
            "method": row["method"], "task_id": row["task_id"],
            "task_type": row["task_type"], "condition": row["condition"],
            "world": row["world"], "seed": row["seed"],
            "server_model_identifiers": ";".join(row["server_model_identifiers"]),
            **result, **audited,
            "model_calls": row["model_calls"], "input_tokens": row["input_tokens"],
            "output_tokens": row["output_tokens"],
            "nominal_cost_usd": row["nominal_cost_usd"],
            "terminal_runtime": row["terminal_runtime"],
        })
    by_method = defaultdict(list)
    for row in scored:
        by_method[row["method"]].append(row)
    if len(by_method["direct_llm"]) != 110 or len(by_method["xenon"]) != 40:
        raise RuntimeError("scored probe does not match frozen scope")
    if any(not row["true_feasible"] for row in by_method["xenon"]):
        raise RuntimeError("XENON probe includes a non-feasible gold instance")

    comparison = defaultdict(list)
    with RESCORE.open() as handle:
        for row in csv.DictReader(handle):
            if row["method"] in EXTERNAL | PIPELINE:
                comparison[row["method"]].append(row)
    table = []
    for method in ("direct_llm", "react", "reflexion", "adam", "xenon"):
        table.append(aggregate(method, comparison[method], "first_round_glm", "all_110"))
    for method in ("naive_rag", "majority_rag", "truthfinder_rag", "verify_all", "arbiter"):
        table.append(aggregate(method, comparison[method], "frozen_pipeline", "all_110"))
    table.append({
        "method": "always_abstain", "source": "analytic", "scope": "all_110",
        "n": 110, "feasible_n": 72, "successes": 38,
        "SR": 38 / 110, "SR_ci95_low": None, "SR_ci95_high": None,
        "feasible_successes": 0, "feasible_task_SR": 0,
        "feasible_SR_ci95_low": 0, "feasible_SR_ci95_high": 0,
        "HTR_all": 0, "HTR_all_ci95_low": None, "HTR_all_ci95_high": None,
        "self_reported_HTR_commit": 0, "self_reported_commit_ci95_low": None,
        "self_reported_commit_ci95_high": None, "overconservatism": 1,
    })
    table.append({
        "method": "oracle", "source": "analytic", "scope": "all_110",
        "n": 110, "feasible_n": 72, "successes": 110,
        "SR": 1, "SR_ci95_low": 1, "SR_ci95_high": 1,
        "feasible_successes": 72, "feasible_task_SR": 1,
        "feasible_SR_ci95_low": 1, "feasible_SR_ci95_high": 1,
        "HTR_all": 0, "HTR_all_ci95_low": 0, "HTR_all_ci95_high": 0,
        "self_reported_HTR_commit": 0, "self_reported_commit_ci95_low": 0,
        "self_reported_commit_ci95_high": 0, "overconservatism": 0,
    })
    direct = aggregate(
        "direct_llm", by_method["direct_llm"], "same_model_replication_glm", "all_110"
    )
    xenon = aggregate(
        "xenon", by_method["xenon"], "same_model_replication_glm", "feasible_sample_40"
    )
    table.extend((direct, xenon))

    judgment_a = direct["feasible_task_SR"] <= 0.10
    judgment_b_count = xenon["feasible_successes"]
    judgment_c_direct = direct["HTR_all"] > 0.155
    judgment_c_xenon = xenon["HTR_all"] > 0.155
    gate = direct["feasible_task_SR"] >= 0.20 or judgment_b_count >= 8
    decisions = [
        {"judgment": "A", "value": direct["feasible_task_SR"], "criterion": "Direct feasible SR <= 0.10",
         "result": judgment_a},
        {"judgment": "B", "value": judgment_b_count, "criterion": "XENON solved count; report 95% upper",
         "result": f'{judgment_b_count}/40; upper={xenon["feasible_SR_ci95_high"]}'},
        {"judgment": "C_direct", "value": direct["HTR_all"], "criterion": "HTR_all > 0.155",
         "result": judgment_c_direct},
        {"judgment": "C_xenon", "value": xenon["HTR_all"], "criterion": "HTR_all > 0.155",
         "result": judgment_c_xenon},
        {"judgment": "step3_gate", "value": int(gate),
         "criterion": "Direct feasible SR >= 0.20 OR XENON solved >= 8/40", "result": gate},
    ]
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "episode_metrics.csv", scored)
    write_csv(OUT / "main_table.csv", table)
    write_csv(OUT / "judgments.csv", decisions)
    model_ids = sorted({identifier for row in scored
                        for identifier in row["server_model_identifiers"].split(";") if identifier})
    if status.get("actual_provider_cost") is None:
        cost_line = "Actual provider cost: unavailable (not recorded as zero)."
    else:
        cost_line = f"Actual provider cost: {status['actual_provider_cost']:.6f}."
    report = [
        "# External same-model robustness replication",
        "",
        f"Actual server model identifier(s): {', '.join(model_ids)}.",
        f"Judgment A: Direct feasible-task SR = {direct['feasible_task_SR']:.4f}; "
        f"{'yes' if judgment_a else 'no'}, it is <= 0.10.",
        f"Judgment B: XENON solved {judgment_b_count}/40 feasible instances; "
        f"episode-bootstrap 95% upper bound = {xenon['feasible_SR_ci95_high']:.4f}.",
        f"Judgment C: Direct HTR_all = {direct['HTR_all']:.4f} and XENON HTR_all = "
        f"{xenon['HTR_all']:.4f}; above 0.155 = {judgment_c_direct}/{judgment_c_xenon}.",
        f"Step-3 gate: {'TRIGGERED' if gate else 'NOT TRIGGERED'}.",
        "",
        f"Successful provider responses including canaries: "
        f"{status['successful_provider_responses']}; infrastructure-error attempts: "
        f"{status['infrastructure_error_attempts']}; total HTTP attempts: "
        f"{status['total_http_attempts']}; "
        f"input/output tokens: {status['input_tokens']}/{status['output_tokens']}.",
        cost_line,
        "",
        "No frozen method, prompt, prior result, or scoring rule was modified.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    print(json.dumps({
        "actual_model_identifiers": model_ids,
        "direct": direct,
        "xenon": xenon,
        "judgments": decisions,
        "gate_triggered": gate,
        "successful_provider_responses": status["successful_provider_responses"],
        "infrastructure_error_attempts": status["infrastructure_error_attempts"],
        "total_http_attempts": status["total_http_attempts"],
        "actual_provider_cost": status.get("actual_provider_cost"),
    }, indent=2))


if __name__ == "__main__":
    main()
