"""Compare the preregistered P6 diagnosis with the repaired development run."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from arbiter.evaluation.p6_execution import _claims


DEVELOPMENT_IDS = {
    "eff_007", "fea_002", "fea_009", "pla_001", "pla_003",
    "pla_008", "pla_009", "pla_011",
}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty analysis table")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _terminal(row: dict[str, Any]) -> str:
    if row["metrics"]["SR"] and row["true_feasible"]:
        return "solved"
    if row["reported_feasible"] is False:
        return "plan_not_found" if row["true_feasible"] else "reported_infeasible"
    primary = sum(int(event["cost"]["primary"]) for event in row["events"])
    return "budget_exhausted" if primary >= 90 else "execution_failed"


def analyze(run_dir: Path, old_diagnosis: Path, output_dir: Path) -> dict[str, Any]:
    episodes = _jsonl(run_dir / "episodes.jsonl")
    if len(episodes) != 144 or {row["task_id"] for row in episodes} != DEVELOPMENT_IDS:
        raise ValueError("round 3 is not the frozen 144-episode development matrix")
    old_rows = _read_csv(old_diagnosis)
    old = {
        (row["method"], row["condition"], row["task_id"], row["world"], int(row["seed"])): row
        for row in old_rows
    }
    bundles: dict[tuple[str, str, int], set[str]] = {}
    for path in sorted((run_dir / "evidence").glob("*.json")):
        bundle = json.loads(path.read_text())
        if bundle["task_id"] not in DEVELOPMENT_IDS:
            raise ValueError("round-3 evidence contains a non-development task")
        seed = int(path.stem.rsplit("__", 1)[1])
        derived, _ = _claims(bundle)
        bundles[(bundle["condition"], bundle["task_id"], seed)] = {
            claim.mechanism_id for claim in derived
        }

    detail = []
    for row in episodes:
        key = (
            row["method"], row["condition"], row["task_id"], row["world"],
            int(row["seed"]),
        )
        before = old[key]
        required = set(filter(None, before["required_mechanism_ids"].split(";")))
        admitted = bundles[(row["condition"], row["task_id"], int(row["seed"]))]
        matched = required & admitted
        denominator = len(required)
        detail.append({
            "method": row["method"], "condition": row["condition"],
            "task_id": row["task_id"], "world": row["world"], "seed": row["seed"],
            "terminal_type": _terminal(row),
            "search_count": row["reader"]["search_count"],
            "unique_chunks": row["reader"]["unique_chunk_count"],
            "admitted_claims": row["reader"]["admitted_claim_count"],
            "closure_numerator": len(matched), "closure_denominator": denominator,
            "closure_rate": len(matched) / denominator if denominator else 1.0,
            "true_feasible": row["true_feasible"],
            "reported_feasible": row["reported_feasible"],
            "SR": row["metrics"]["SR"], "HTR": row["metrics"]["HTR"],
            "required_mechanism_ids": ";".join(sorted(required)),
            "matched_mechanism_ids": ";".join(sorted(matched)),
        })
    _write_csv(output_dir / "episodes.csv", detail)

    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in detail:
        grouped[(row["method"], row["condition"])].append(row)
    summary_rows = []
    terminals = (
        "solved", "reported_infeasible", "plan_not_found",
        "budget_exhausted", "execution_failed",
    )
    for (method, condition), rows in sorted(grouped.items()):
        summary_rows.append({
            "method": method, "condition": condition, "episodes": len(rows),
            **{name: sum(row["terminal_type"] == name for row in rows) for name in terminals},
            "mean_closure": sum(row["closure_rate"] for row in rows) / len(rows),
            "mean_searches": sum(int(row["search_count"]) for row in rows) / len(rows),
            "mean_unique_chunks": sum(int(row["unique_chunks"]) for row in rows) / len(rows),
            "mean_admitted_claims": sum(int(row["admitted_claims"]) for row in rows) / len(rows),
            "SR": sum(int(row["SR"]) for row in rows) / len(rows),
            "HTR": sum(int(row["HTR"]) for row in rows) / len(rows),
        })
    _write_csv(output_dir / "method_condition.csv", summary_rows)

    comparison = []
    for stage, rows in (("before", old_rows), ("after", detail)):
        by_method: dict[str, list[Any]] = defaultdict(list)
        for row in rows:
            by_method[row["method"]].append(row)
        for method, values in sorted(by_method.items()):
            comparison.append({
                "stage": stage, "method": method, "episodes": len(values),
                "SR": sum(float(row["SR"]) for row in values) / len(values),
                "HTR": sum(float(row.get("HTR", row.get("HTR_recorded", 0))) for row in values) / len(values),
                "mean_closure": sum(float(row["closure_rate"]) for row in values) / len(values),
            })
    _write_csv(output_dir / "before_after.csv", comparison)

    mismatch_htr = {
        condition: next(
            row["HTR"] for row in summary_rows
            if row["method"] == "naive_rag" and row["condition"] == condition
        )
        for condition in ("mismatched_old2new", "mismatched_new2old")
    }
    result = {
        "schema_version": 1,
        "episodes": len(detail),
        "development_task_ids": sorted(DEVELOPMENT_IDS),
        "evaluation_tasks_read_or_run": False,
        "llm_calls": 0,
        "naive_mismatch_HTR": mismatch_htr,
        "mismatch_htr_threshold": 0.20,
        "pipeline_closed": all(value >= 0.20 for value in mismatch_htr.values()),
        "stop_reason": "naive_mismatch_HTR_below_0.20",
        "before_after": comparison,
        "method_condition": summary_rows,
    }
    (output_dir / "summary.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n",
    )
    return result


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, default=Path("artifacts/p6_round3_pipeline"))
    parser.add_argument(
        "--old-diagnosis", type=Path, default=Path("artifacts/p6_diagnosis/episodes.csv"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/p6_round3_pipeline/diagnosis"),
    )
    args = parser.parse_args()
    result = analyze(args.run_dir, args.old_diagnosis, args.output_dir)
    print(json.dumps({
        "pipeline_closed": result["pipeline_closed"],
        "naive_mismatch_HTR": result["naive_mismatch_HTR"],
        "stop_reason": result["stop_reason"],
    }, sort_keys=True))
