"""Generate deterministic audits and secondary summaries for one main run."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from arbiter.evaluation.p6_matrix import coverage_conflict_ids


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
METHODS = {
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "oracle", "arbiter",
}
MECHANISM_ACTIONS = {"mine", "craft_grid", "smelt", "check_name"}


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _mean(rows: list[dict[str, Any]], field: str) -> float:
    return sum(float(row["metrics"][field]) for row in rows) / len(rows)


def _terminal(row: dict[str, Any]) -> str:
    if row["metrics"]["SR"] and row["true_feasible"]:
        return "solved"
    if row["reported_feasible"] is False:
        return "plan_not_found" if row["true_feasible"] else "reported_infeasible"
    return "budget_exhausted" if row["metrics"]["primary_cost"] >= 90 else "execution_failed"


def _task_type(task_id: str, tasks: dict[str, dict[str, Any]]) -> str:
    return str(tasks[task_id]["type"])


def _aggregate_rows(rows: list[dict[str, Any]], key_fields: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row[field]) for field in key_fields)].append(row)
    output = []
    for key, group in sorted(groups.items()):
        output.append({
            **dict(zip(key_fields, key)), "episodes": len(group),
            "SR": _mean(group, "SR"),
            "HTR_commit": _mean(group, "HTR_commit"),
            "HTR_all": _mean(group, "HTR_all"),
            "probe_count": _mean(group, "probe_count"),
            "probe_cost": _mean(group, "probe_cost"),
            "primary_cost": _mean(group, "primary_cost"),
            "searches": sum(float(row["reader"]["search_count"]) for row in group) / len(group),
        })
    return output


def _secondary(
    rows: list[dict[str, Any]], tasks: dict[str, dict[str, Any]],
    conflict_vectors: dict[str, dict[str, bool]], one_sided: frozenset[str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    sys.path.insert(0, str(WEBCRAFT))
    from metrics.episode import aggregate, episode_metrics

    scored: list[tuple[dict[str, Any], dict[str, Any]]] = []
    for row in rows:
        task = tasks[row["task_id"]]
        mechanisms = iter(row["used_mechanism_ids"])
        vectors: dict[int, dict[str, bool]] = {}
        for index, event in enumerate(row["events"]):
            if event.get("method") not in MECHANISM_ACTIONS:
                continue
            mechanism = next(mechanisms)
            if mechanism in conflict_vectors:
                vectors[index] = conflict_vectors[mechanism]
        try:
            next(mechanisms)
        except StopIteration:
            pass
        else:
            raise ValueError("action/mechanism alignment changed")
        metrics = episode_metrics(
            row["events"], goal=task["target"], world=row["world"],
            true_feasible=row["true_feasible"], success=bool(row["metrics"]["SR"]),
            reported_feasible=row["reported_feasible"], version_hashes={},
            mechanism_vectors=vectors, oracle_cost=task["oracle_cost"][row["world"]],
            task_type=task["type"], initial_inventory={
                item["name"]: int(item["count"])
                for item in task["standard_items"] + task.get("task_supplements", [])
            },
        )
        scored.append((row, metrics))

    overall = []
    by_method: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row, metrics in scored:
        by_method[row["method"]].append(metrics)
    for method, group in sorted(by_method.items()):
        overall.append({"method": method, **aggregate(group)})

    cells = []
    for subset in ("all", "two_sided", "one_sided"):
        grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
        for row, metrics in scored:
            is_one = bool(one_sided.intersection(row["conflict_mechanism_ids"]))
            if subset != "all" and is_one != (subset == "one_sided"):
                continue
            grouped[(row["method"], row["condition"], row["world"])].append(metrics)
        for (method, condition, world), group in sorted(grouped.items()):
            summary = aggregate(group)
            cells.append({
                "subset": subset, "method": method, "condition": condition,
                "world": world, "episodes": len(group), "OVR": summary["OVR"],
                "OVR_n": summary["OVR_n"],
                "OVR_missing": len(group) - int(summary["OVR_n"]),
            })
    return overall, cells


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--manifest", type=Path, required=True)
    parser.add_argument("--evidence-dir", type=Path, required=True)
    parser.add_argument("--tasks", type=Path, default=WEBCRAFT / "data/tasks.jsonl")
    parser.add_argument("--conflicts", type=Path, default=WEBCRAFT / "data/conflicts.jsonl")
    parser.add_argument(
        "--coverage", type=Path,
        default=WEBCRAFT / "reports/corpus_stats/coverage_p1.csv",
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()

    rows = _jsonl(args.input)
    manifest = json.loads(args.manifest.read_text())
    tasks = {
        row["id"]: row for row in _jsonl(args.tasks)
        if row.get("split") == "eval"
    }
    conflicts = _jsonl(args.conflicts)
    conflict_vectors = {row["id"]: row["truth_vector"] for row in conflicts}
    if len(rows) != 6732 or len(tasks) != 34 or manifest.get("episodes") != 6732:
        raise ValueError("main matrix cardinality mismatch")
    keys = [
        (row["reading_mode"], row["condition"], row["world"], row["task_id"],
         row["method"], int(row["seed"])) for row in rows
    ]
    if len(set(keys)) != len(rows) or {row["method"] for row in rows} != METHODS:
        raise ValueError("main matrix is duplicate or incomplete")
    modes = {row["reading_mode"] for row in rows}
    if len(modes) != 1 or modes != {manifest["reading_mode"]}:
        raise ValueError("reading mode does not match manifest")

    typed = [{**row, "task_type": _task_type(row["task_id"], tasks)} for row in rows]
    _write_csv(args.output_dir / "aggregate.csv", _aggregate_rows(typed, ("method",)))
    _write_csv(
        args.output_dir / "task_type_success.csv",
        _aggregate_rows(typed, ("method", "task_type")),
    )
    one_sided = coverage_conflict_ids(args.coverage, "one_sided")
    subset_rows = []
    for subset in ("all", "two_sided", "one_sided"):
        selected = [
            row for row in typed
            if subset == "all" or bool(
                one_sided.intersection(row["conflict_mechanism_ids"])
            ) == (subset == "one_sided")
        ]
        subset_rows.extend({"subset": subset, **row} for row in _aggregate_rows(
            selected, ("method",),
        ))
    _write_csv(args.output_dir / "subset_aggregate.csv", subset_rows)
    ablation = [
        row for row in _aggregate_rows(typed, ("method", "condition"))
        if row["method"] in {"arbiter", "arbiter_shell_naive"}
        and row["condition"] in {"mixed", "polluted"}
    ]
    _write_csv(args.output_dir / "ablation_table.csv", ablation)
    terminals = []
    for (method, condition), group in sorted(_group(typed, "method", "condition").items()):
        counts = Counter(_terminal(row) for row in group)
        terminals.append({
            "method": method, "condition": condition, "episodes": len(group),
            **{name: counts[name] for name in (
                "solved", "reported_infeasible", "plan_not_found",
                "budget_exhausted", "execution_failed",
            )},
        })
    _write_csv(args.output_dir / "terminal_counts.csv", terminals)
    secondary, secondary_cells = _secondary(
        rows, tasks, conflict_vectors, one_sided,
    )
    _write_csv(args.output_dir / "secondary_metrics.csv", secondary)
    _write_csv(
        args.output_dir / "secondary_by_cell_subset.csv", secondary_cells,
    )

    evidence = sorted(args.evidence_dir.glob("*.json"))
    bundles = [json.loads(path.read_text()) for path in evidence]
    seed_pairs: dict[tuple[Any, ...], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in rows:
        key = (row["condition"], row["world"], row["task_id"], row["method"])
        seed_pairs[key][int(row["seed"])] = row
    metric_seed_equal = all(
        pair[20261006]["metrics"] == pair[20261007]["metrics"]
        for pair in seed_pairs.values()
    )
    audit = {
        "schema_version": 1, "reading_mode": next(iter(modes)),
        "episodes": len(rows), "unique_episode_keys": len(set(keys)),
        "tasks": len({row["task_id"] for row in rows}),
        "cells": len({(row["condition"], row["world"]) for row in rows}),
        "methods": sorted({row["method"] for row in rows}),
        "seeds": sorted({int(row["seed"]) for row in rows}),
        "oracle_episodes": sum(row["method"] == "oracle" for row in rows),
        "oracle_successes": sum(
            row["method"] == "oracle" and row["metrics"]["SR"] == 1 for row in rows
        ),
        "evidence_bundles": len(bundles),
        "claims_cache_only": bool(bundles) and all(
            bundle.get("claim_source") == "precomputed_cache" for bundle in bundles
        ),
        "fallback_used": any(
            bundle.get("claim_source") != "precomputed_cache" for bundle in bundles
        ),
        "model_calls": sum(
            int(event["cost"].get("model_calls", 0))
            for row in rows for event in row["events"]
        ),
        "metric_seed_pairs_identical": metric_seed_equal,
        "seed_effect_statement": (
            "seed has no effect on recorded metrics" if metric_seed_equal
            else "seed affects at least one recorded metric"
        ),
        "episodes_sha256": _digest(args.input),
        "manifest_sha256": _digest(args.manifest),
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "fairness_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(audit, sort_keys=True))


def _group(rows: list[dict[str, Any]], *fields: str) -> dict[tuple[str, ...], list[dict[str, Any]]]:
    groups: dict[tuple[str, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(str(row[field]) for field in fields)].append(row)
    return groups


if __name__ == "__main__":
    main()
