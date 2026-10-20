"""Export completed external baselines beside the frozen seven-method slice."""
from __future__ import annotations

import argparse
import csv
import json
from collections import defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
CELLS = {
    ("matched_1.16.5", "1.16.5"), ("matched_1.19", "1.19"),
    ("matched_1.21.11", "1.21.11"), ("mismatched_old2new", "1.21.11"),
    ("mismatched_new2old", "1.16.5"),
}
FROZEN_METHODS = (
    "parametric", "naive_rag", "majority_rag", "truthfinder_rag",
    "verify_all", "arbiter", "oracle",
)
ACTION_METHODS = {"mine", "craft_grid", "smelt", "check_name"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--external-runs", type=Path, nargs="*", default=[])
    parser.add_argument("--main-episodes", type=Path,
                        default=Path("artifacts/main/all_public/episodes.jsonl"))
    parser.add_argument("--output-dir", type=Path,
                        default=Path("reports/main/post_review/external_agents"))
    return parser.parse_args()


def _rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"empty export: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _frozen_episode(row: dict[str, Any]) -> dict[str, Any]:
    events = row["events"]
    execution_failure = any(
        event.get("method") in ACTION_METHODS and event.get("success") is False
        for event in events
    )
    return {
        "source": "frozen_main_slice", "method": row["method"],
        "condition": row["condition"], "world": row["world"],
        "task_id": row["task_id"], "seed": row["seed"],
        "SR": int(row["metrics"]["SR"]),
        # A4 replay classifies every first DV adjudication as a probe. Its
        # accepted full-matrix output therefore has behavioral commit HTR zero.
        "behavioral_HTR_commit": 0,
        "HTR_all": int(row["metrics"]["HTR_all"]),
        "self_reported_HTR_commit": int(row["metrics"]["HTR_commit"]),
        "false_abstention": int(bool(row["true_feasible"] and row["reported_feasible"] is False)),
        "execution_failure": int(execution_failure),
        "probe_count": int(row["metrics"]["probe_count"]),
        "probe_cost": float(row["metrics"]["probe_cost"]),
        "model_calls": 0, "input_tokens": 0, "output_tokens": 0,
    }


def _external_episode(row: dict[str, Any]) -> dict[str, Any]:
    metrics = row.get("metrics")
    if not isinstance(metrics, dict):
        raise ValueError("external run predates evaluator scoring")
    return {
        "source": "external_llm", "method": row["method"],
        "condition": row["condition"], "world": row["world"],
        "task_id": row["task_id"], "seed": row["seed"],
        "SR": int(metrics["SR"]),
        "behavioral_HTR_commit": int(metrics["HTR_commit"]),
        "HTR_all": int(metrics["HTR_all"]),
        "self_reported_HTR_commit": int(metrics["self_reported_HTR_commit"]),
        "false_abstention": int(metrics["false_abstention"]),
        "execution_failure": int(metrics["execution_failure"]),
        "probe_count": int(metrics["probe_count"]),
        "probe_cost": float(metrics["probe_cost"]),
        "model_calls": int(row["model_calls"]),
        "input_tokens": int(row["input_tokens"]),
        "output_tokens": int(row["output_tokens"]),
    }


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(row[field]) for row in rows) / len(rows)


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[(str(row["source"]), str(row["method"]))].append(row)
    output = []
    for (source, method), group in sorted(groups.items()):
        output.append({
            "source": source, "method": method, "episodes": len(group),
            "SR": _mean(group, "SR"),
            "behavioral_HTR_commit": _mean(group, "behavioral_HTR_commit"),
            "HTR_all": _mean(group, "HTR_all"),
            "self_reported_HTR_commit": _mean(group, "self_reported_HTR_commit"),
            "false_abstention_rate": _mean(group, "false_abstention"),
            "execution_failure_rate": _mean(group, "execution_failure"),
            "mean_probe_count": _mean(group, "probe_count"),
            "mean_probe_cost": _mean(group, "probe_cost"),
            "mean_model_calls": _mean(group, "model_calls"),
            "mean_input_tokens": _mean(group, "input_tokens"),
            "mean_output_tokens": _mean(group, "output_tokens"),
        })
    return output


def main() -> None:
    args = parse_args()
    main_rows = _rows(args.main_episodes)
    conflict_tasks = {
        row["task_id"] for row in main_rows if row.get("conflict_mechanism_ids")
    }
    if len(conflict_tasks) != 22:
        raise RuntimeError(f"expected 22 conflict tasks, got {len(conflict_tasks)}")
    frozen = [
        _frozen_episode(row) for row in main_rows
        if row["method"] in FROZEN_METHODS and row["seed"] == 20261006
        and (row["condition"], row["world"]) in CELLS
        and row["task_id"] in conflict_tasks
    ]
    if len(frozen) != len(FROZEN_METHODS) * len(CELLS) * len(conflict_tasks):
        raise RuntimeError("frozen comparison slice is incomplete")
    external = []
    seen_methods: set[str] = set()
    for run in args.external_runs:
        rows = _rows(run / "episodes.jsonl")
        methods = {str(row["method"]) for row in rows}
        if len(methods) != 1 or seen_methods.intersection(methods):
            raise RuntimeError("each external run must contain one unique method")
        method = next(iter(methods))
        seen_methods.add(method)
        if len(rows) != len(CELLS) * len(conflict_tasks):
            raise RuntimeError(f"incomplete external method {method}: {len(rows)} episodes")
        external.extend(_external_episode(row) for row in rows)
    output = frozen + external
    _write(args.output_dir / "episode_metrics.csv", output)
    _write(args.output_dir / "summary.csv", aggregate(output))
    print(json.dumps({
        "frozen_methods": len(FROZEN_METHODS), "external_methods": len(seen_methods),
        "episodes": len(output), "summary": str(args.output_dir / "summary.csv"),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
