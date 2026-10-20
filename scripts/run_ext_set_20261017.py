#!/usr/bin/env python3
"""Run and summarize the preregistered deterministic EXT matrix."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
from collections import defaultdict
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
ARBITER = Path("/root/autodl-tmp/arbiter")
sys.path.insert(0, str(ARBITER / "src"))
sys.path.insert(0, str(ROOT))

from arbiter.evaluation.p6_execution import (  # noqa: E402
    _MECHANISM_ACTIONS,
    _claims,
    _compiler,
    run_matrix,
)
from arbiter.evaluation.p6_matrix import P6Job  # noqa: E402


TASK_PATH = ROOT / "data/ext/ext_tasks_20261017.jsonl"
FREEZE_PATH = ROOT / "config/ext_freeze_manifest_20261017.json"
OUTPUT = ROOT / "reports/paperA/ext"
INDEX_DIR = ROOT / "corpus/indexes/formal_p1"
PIN_PATH = OUTPUT / "webcraft_ext_pin_20261017.json"
PRIOR_PATH = ARBITER / "artifacts/prior/frozen_prior_bundle.json"
CELLS = (
    ("matched_1.16.5", "1.16.5"),
    ("matched_1.19", "1.19"),
    ("matched_1.21.11", "1.21.11"),
    ("mismatched_old2new", "1.21.11"),
    ("mismatched_new2old", "1.16.5"),
)
METHODS = (
    "parametric",
    "probe_then_parametric",
    "naive_rag",
    "majority_rag",
    "truthfinder_rag",
    "verify_all",
    "arbiter_shell_naive",
    "arbiter",
    "oracle",
    "probe_then_rag",
)
SEEDS = (20261017,)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def csv_write(path: Path, rows: list[dict[str, Any]], fields: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({field: row.get(field, "") for field in fields})


def verify_freeze() -> dict[str, Any]:
    manifest = json.loads(FREEZE_PATH.read_text())
    mismatches = []
    for name, expected in manifest["inputs"].items():
        path = ROOT / name if not name.startswith("/") else Path(name)
        actual = sha256(path) if path.is_file() else "missing"
        if actual != expected:
            mismatches.append({"path": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"EXT freeze mismatch: {mismatches}")
    return manifest


def load_prior() -> dict[str, Any]:
    from arbiter.agent.aggregation import operator_from_assertion

    payload = json.loads(PRIOR_PATH.read_text())
    return {
        "operators": [operator_from_assertion(row) for row in payload["assertions"]],
        "samples": payload["samples"],
    }


def jobs(tasks: list[dict[str, Any]]) -> list[P6Job]:
    rows = [
        P6Job(condition, task["id"], world, method, seed)
        for condition, world in CELLS
        for task in tasks
        for method in METHODS
        for seed in SEEDS
    ]
    expected = len(CELLS) * len(tasks) * len(METHODS) * len(SEEDS)
    if len(rows) != expected:
        raise AssertionError("EXT matrix cardinality changed")
    return rows


def write_run_manifest(rows: list[P6Job], tasks: list[dict[str, Any]]) -> None:
    payload = {
        "schema_version": 1,
        "scope": "post_review_ext_only",
        "included_in_frozen_main_matrix": False,
        "reading_mode": "all_public",
        "cells": [list(row) for row in CELLS],
        "methods": list(METHODS),
        "seeds": list(SEEDS),
        "task_ids": [task["id"] for task in tasks],
        "episodes": len(rows),
        "jobs": [asdict(row) for row in rows],
        "freeze_manifest_sha256": sha256(FREEZE_PATH),
    }
    path = OUTPUT / "run_manifest.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def conflict_vectors() -> dict[str, dict[str, bool]]:
    return {
        row["id"]: row["truth_vector"]
        for row in jsonl(ROOT / "data/conflicts.jsonl")
    }


def bundle_mechanisms(path: Path, normalizer: Any) -> set[str]:
    bundle = json.loads(path.read_text())
    derived, _ = _claims(bundle, normalizer=normalizer)
    return {row.mechanism_id for row in derived}


def terminal(record: dict[str, Any]) -> str:
    success = bool(record["metrics"]["SR"])
    feasible = bool(record["true_feasible"])
    reported = record["reported_feasible"]
    if success:
        return "success"
    if feasible and reported is False:
        return "overconservative"
    if feasible and reported is True:
        return "execution_failure"
    if feasible:
        return "undetermined_failure"
    if reported is False:
        return "correct_infeasible"
    if reported is True:
        return "false_feasible"
    return "undetermined_infeasible"


def episode_rows(records: list[dict[str, Any]], tasks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    task_map = {task["id"]: task for task in tasks}
    vectors = conflict_vectors()
    normalizer, _ = _compiler(ROOT)
    evidence_cache: dict[tuple[str, str, int], set[str]] = {}
    rows = []
    for record in records:
        task = task_map[record["task_id"]]
        evidence_key = (record["condition"], record["task_id"], int(record["seed"]))
        if evidence_key not in evidence_cache:
            evidence_path = OUTPUT / "run/all_public/evidence" / (
                f"{record['condition']}__{record['task_id']}__{record['seed']}.json"
            )
            evidence_cache[evidence_key] = bundle_mechanisms(evidence_path, normalizer)
        stale_ids = {
            conflict_id for conflict_id in task["conflict_ids"]
            if not vectors[conflict_id][record["world"]]
            and any(vectors[conflict_id].values())
        }
        mechanism_events = [
            event for event in record["events"]
            if event.get("method") in _MECHANISM_ACTIONS
        ]
        used = list(record["used_mechanism_ids"])
        if len(mechanism_events) != len(used):
            raise RuntimeError("action/mechanism alignment changed")
        independent_commit = any(
            mechanism in stale_ids and event.get("intent") != "probe"
            for mechanism, event in zip(used, mechanism_events)
        )
        independent_execution = any(mechanism in stale_ids for mechanism in used)
        result = {
            "task_id": record["task_id"],
            "independent_mechanism_id": task["independent_mechanism_id"],
            "condition": record["condition"],
            "world": record["world"],
            "method": record["method"],
            "seed": record["seed"],
            "true_feasible": int(bool(record["true_feasible"])),
            "reported_feasible": record["reported_feasible"],
            "terminal": terminal(record),
            "SR": int(record["metrics"]["SR"]),
            "feasible_SR": int(record["metrics"]["SR"]) if record["true_feasible"] else "",
            "overconservative": int(
                bool(record["true_feasible"] and record["reported_feasible"] is False)
            ),
            "execution_failure": int(
                bool(record["true_feasible"] and record["reported_feasible"] is True
                     and not record["metrics"]["SR"])
            ),
            "plan_acceptance_stale_any": int(record["metrics"]["HTR_commit"]),
            "plan_acceptance_independent": int(independent_commit),
            "stale_specific_query_exposure": int(bool(stale_ids & evidence_cache[evidence_key])),
            "stale_specific_execution_exposure": int(independent_execution),
            "semantic_actions": len(mechanism_events),
            "primary_cost": float(record["metrics"]["primary_cost"]),
            "probe_count": int(record["metrics"]["probe_count"]),
            "probe_cost": float(record["metrics"]["probe_cost"]),
            "search_calls": int(record["reader"].get("search_count", 0)),
        }
        rows.append(result)
    return sorted(rows, key=lambda row: (
        row["independent_mechanism_id"], row["condition"], row["method"]
    ))


def aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    output = []
    for key, values in sorted(groups.items()):
        feasible = [row for row in values if row["true_feasible"]]
        output.append({
            **dict(zip(keys, key)),
            "n": len(values),
            "feasible_n": len(feasible),
            "SR": mean(row["SR"] for row in values),
            "feasible_SR": mean(row["SR"] for row in feasible) if feasible else "",
            "overconservative_rate": mean(row["overconservative"] for row in feasible) if feasible else "",
            "execution_failure_rate": mean(row["execution_failure"] for row in feasible) if feasible else "",
            "plan_acceptance_stale_any": mean(row["plan_acceptance_stale_any"] for row in values),
            "plan_acceptance_independent": mean(row["plan_acceptance_independent"] for row in values),
            "stale_specific_query_exposure": mean(row["stale_specific_query_exposure"] for row in values),
            "stale_specific_execution_exposure": mean(row["stale_specific_execution_exposure"] for row in values),
            "semantic_actions_mean": mean(row["semantic_actions"] for row in values),
            "primary_cost_mean": mean(row["primary_cost"] for row in values),
            "probe_count_mean": mean(row["probe_count"] for row in values),
            "probe_cost_mean": mean(row["probe_cost"] for row in values),
            "search_calls_mean": mean(row["search_calls"] for row in values),
        })
    return output


def judgments(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {
        (row["independent_mechanism_id"], row["method"], row["condition"]): row
        for row in aggregate(rows, ("independent_mechanism_id", "method", "condition"))
    }
    output = []
    for mechanism in sorted({row["independent_mechanism_id"] for row in rows}):
        method_rows = []
        old2new_increased = []
        new2old_increased = []
        for method in METHODS:
            matched_new = indexed[(mechanism, method, "matched_1.21.11")]
            old2new = indexed[(mechanism, method, "mismatched_old2new")]
            matched_old = indexed[(mechanism, method, "matched_1.16.5")]
            new2old = indexed[(mechanism, method, "mismatched_new2old")]
            delta_old2new = (
                old2new["plan_acceptance_independent"]
                - matched_new["plan_acceptance_independent"]
            )
            delta_new2old = (
                new2old["plan_acceptance_independent"]
                - matched_old["plan_acceptance_independent"]
            )
            if delta_old2new > 0:
                old2new_increased.append(method)
            if delta_new2old > 0:
                new2old_increased.append(method)
            method_rows.append({
                "method": method,
                "old2new_delta": delta_old2new,
                "new2old_delta": delta_new2old,
            })
        output.append({
            "independent_mechanism_id": mechanism,
            "old2new_methods_increased": ";".join(old2new_increased) or "none",
            "new2old_methods_increased": ";".join(new2old_increased) or "none",
            "per_method_deltas_json": json.dumps(method_rows, sort_keys=True),
            "judgment": (
                "mismatch_raises_plan_acceptance_outside_MC1"
                if old2new_increased or new2old_increased
                else "no_observed_mismatch_increase"
            ),
        })
    return output


def write_report(
    rows: list[dict[str, Any]], mechanism_table: list[dict[str, Any]],
    judgment_rows: list[dict[str, Any]],
) -> None:
    by_key = {
        (row["independent_mechanism_id"], row["condition"], row["method"]): row
        for row in mechanism_table
    }
    lines = [
        "# W6 EXT independent-mechanism diagnostic set",
        "",
        "> Post-review addition with explicit extension diagnostic packages. These results are separate from the frozen main matrix and do not change any previously reported number.",
        "",
        f"- Tasks: {len({row['task_id'] for row in rows})}.",
        f"- Independent mechanisms: {len({row['independent_mechanism_id'] for row in rows})}.",
        f"- Deterministic episodes: {len(rows)} (zero LLM calls).",
        "- Reading mode: `all_public`; cells: three matched and two mismatched single-world cells.",
        "",
        "## Per-mechanism judgments",
        "",
    ]
    for judgment in judgment_rows:
        mechanism = judgment["independent_mechanism_id"]
        lines.append(f"### `{mechanism}`")
        lines.append("")
        lines.append(f"- Judgment: `{judgment['judgment']}`.")
        lines.append(
            "- Methods with an independent-mechanism plan-acceptance increase in old-to-new mismatch: "
            f"{judgment['old2new_methods_increased']}."
        )
        lines.append(
            "- Methods with an independent-mechanism plan-acceptance increase in new-to-old mismatch: "
            f"{judgment['new2old_methods_increased']}."
        )
        for method in METHODS:
            matched_old = by_key[(mechanism, "matched_1.16.5", method)]
            new2old = by_key[(mechanism, "mismatched_new2old", method)]
            matched_new = by_key[(mechanism, "matched_1.21.11", method)]
            old2new = by_key[(mechanism, "mismatched_old2new", method)]
            lines.append(
                f"- `{method}`: new-to-old {matched_old['plan_acceptance_independent']:.3f} -> "
                f"{new2old['plan_acceptance_independent']:.3f}; old-to-new "
                f"{matched_new['plan_acceptance_independent']:.3f} -> "
                f"{old2new['plan_acceptance_independent']:.3f}."
            )
        lines.append("")
    lines.extend([
        "## Metric notes",
        "",
        "`plan_acceptance_stale_any` is the existing declared non-probe stale-plan metric. `plan_acceptance_independent` restricts it to the preregistered EXT conflict ID. Query and execution exposure are likewise restricted to that ID. Real cost is reported as semantic actions, primary semantic cost, probe count/cost, and search calls.",
        "",
        "The optional external Direct/ReAct run was not executed. With 4 tasks x 5 cells = 20 instances per method and approximately 10 calls per instance, the estimate is about 200 calls per method, or 400 calls total.",
    ])
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n")


def summarize(tasks: list[dict[str, Any]]) -> None:
    episode_path = OUTPUT / "run/all_public/episodes.jsonl"
    records = jsonl(episode_path)
    expected = len(tasks) * len(CELLS) * len(METHODS) * len(SEEDS)
    if len(records) != expected:
        raise RuntimeError(f"EXT coverage mismatch: {len(records)} != {expected}")
    keys = {
        (row["condition"], row["task_id"], row["world"], row["method"], row["seed"])
        for row in records
    }
    if len(keys) != expected:
        raise RuntimeError("EXT results contain duplicate cells")
    rows = episode_rows(records, tasks)
    episode_fields = list(rows[0])
    csv_write(OUTPUT / "ext_episode_metrics.csv", rows, episode_fields)
    mechanism_table = aggregate(
        rows, ("independent_mechanism_id", "condition", "world", "method")
    )
    csv_write(
        OUTPUT / "ext_results_by_mechanism.csv",
        mechanism_table,
        list(mechanism_table[0]),
    )
    overall = aggregate(rows, ("condition", "world", "method"))
    csv_write(OUTPUT / "ext_results_overall.csv", overall, list(overall[0]))
    judgment_rows = judgments(rows)
    csv_write(
        OUTPUT / "mechanism_judgments.csv", judgment_rows, list(judgment_rows[0])
    )
    write_report(rows, mechanism_table, judgment_rows)
    hashes = {
        str(path.relative_to(ROOT)): sha256(path)
        for path in sorted(OUTPUT.glob("*.csv")) + [OUTPUT / "REPORT.md"]
    }
    (OUTPUT / "OUTPUT_HASHES.json").write_text(
        json.dumps(hashes, indent=2, sort_keys=True) + "\n"
    )


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--check-only", action="store_true")
    parser.add_argument("--summarize-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    freeze = verify_freeze()
    tasks = jsonl(TASK_PATH)
    if len(tasks) != freeze["task_count"]:
        raise RuntimeError("EXT task count differs from freeze")
    matrix = jobs(tasks)
    write_run_manifest(matrix, tasks)
    if args.check_only:
        print(json.dumps({
            "status": "EXT_PREREG_CHECK_OK",
            "tasks": len(tasks),
            "mechanisms": freeze["independent_mechanism_count"],
            "episodes": len(matrix),
        }, sort_keys=True))
        return 0
    if not args.summarize_only:
        run_matrix(
            matrix,
            tasks,
            root=ROOT,
            pin_path=PIN_PATH,
            index_dir=str(INDEX_DIR),
            output_dir=OUTPUT / "run/all_public",
            top_k=10,
            reading_mode="all_public",
            prior_bundle=load_prior(),
        )
    summarize(tasks)
    print(json.dumps({
        "status": "EXT_COMPLETE",
        "tasks": len(tasks),
        "mechanisms": freeze["independent_mechanism_count"],
        "episodes": len(matrix),
        "report": str((OUTPUT / "REPORT.md").relative_to(ROOT)),
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
