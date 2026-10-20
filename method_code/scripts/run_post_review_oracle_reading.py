"""Run the deterministic, post-review oracle-reading evidence supplement.

This script is evaluator-only.  It does not alter frozen code or the reported
main-experiment artifacts, and it makes no LLM or cache-fallback calls.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from arbiter.checkpoint import require_memory
from arbiter.evaluation.main_matrix import MAIN_CELLS, METHOD_SETS, build_main_matrix
from arbiter.evaluation.p6_execution import _claims, _compiler, load_conflict_vectors, run_episode
from arbiter.evaluation.p6_matrix import P6Job, coverage_conflict_ids, result_subsets_with_control
from arbiter.evaluation.post_review import selected_gold_bundle
from arbiter.evaluation.reporting import EpisodeRecord, aggregate_main_table, world_breakdown
from arbiter.pinning import verify_webcraft_pin
# The script directory is on sys.path when this file is executed directly.
from run_main import load_evaluation_tasks, load_prior_bundle


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
OUTPUT = ROOT / "artifacts/post_review/oracle_reading"
REPORTS = ROOT / "reports/main/post_review"
SEED = 20261006


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"empty post-review table: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _five_conditions(records: list[EpisodeRecord]) -> list[EpisodeRecord]:
    groups = {
        "matched_1.16.5": "matched", "matched_1.19": "matched",
        "matched_1.21.11": "matched", "mismatched_old2new": "mismatched_old2new",
        "mismatched_new2old": "mismatched_new2old", "mixed": "mixed", "polluted": "polluted",
    }
    from dataclasses import replace
    return [replace(row, condition=groups[row.condition]) for row in records]


def _with_cell(records: list[EpisodeRecord]) -> list[EpisodeRecord]:
    from dataclasses import replace
    return [replace(row, condition=f"{row.condition}@@{row.world}") for row in records]


def _closure_rows(
    raw_tasks: dict[str, dict[str, Any]], bundles: dict[tuple[str, str], dict[str, Any]],
    normalizer: Any, mineable: set[str], requirements_from_task: Any, claim_matches: Any,
) -> list[dict[str, Any]]:
    rows = []
    for (condition, task_id), bundle in sorted(bundles.items()):
        required = requirements_from_task(raw_tasks[task_id])
        supplied = 0
        compiled = 0
        for requirement in required:
            matching = [claim for claim in bundle["claims"] if claim_matches(requirement, claim)]
            supplied += int(bool(matching))
            mini = {**bundle, "claims": matching,
                    "search_rows": [row for row in bundle["search_rows"]
                                    if row["chunk_id"] in {claim["chunk_id"] for claim in matching}]}
            _, operators = _claims(mini, normalizer=normalizer) if matching else ([], [])
            compiled += int(bool(operators))
        rows.append({
            "condition": condition, "task_id": task_id, "required_mechanisms": len(required),
            "supplied_mechanisms": supplied, "compiled_mechanisms": compiled,
        })
    return rows


def main() -> None:
    # This is an explicit author-authorized exception for deterministic checks.
    require_memory(512 * 1024**2, reserve_bytes=int(1.8 * 1024**3))
    verify_webcraft_pin(ROOT / "config/webcraft_pin.json", WEBCRAFT, require_ready=True)
    if str(WEBCRAFT) not in sys.path:
        sys.path.insert(0, str(WEBCRAFT))
    from corpus.solvability_audit import claim_matches, requirements_from_task

    public_tasks = load_evaluation_tasks(
        WEBCRAFT / "data/tasks.jsonl", WEBCRAFT / "config/resource_model.json",
    )
    raw_tasks = {row["id"]: row for row in _jsonl(WEBCRAFT / "data/tasks.jsonl")
                 if row.get("split") == "eval"}
    if len(public_tasks) != 34 or set(raw_tasks) != {row["id"] for row in public_tasks}:
        raise RuntimeError("oracle-reading requires exactly the frozen 34-task evaluation split")
    prior, prior_sha = load_prior_bundle(ROOT / "artifacts/prior/frozen_prior_bundle.json")
    jobs = [job for job in build_main_matrix(
        (row["id"] for row in public_tasks), "baselines_plus_arbiter",
    ) if job.seed == SEED]
    jobs.extend(
        P6Job(condition, task["id"], world, "probe_then_rag", SEED)
        for condition, world in MAIN_CELLS for task in public_tasks
    )
    methods = METHOD_SETS["baselines_plus_arbiter"] + ("probe_then_rag",)
    if len(jobs) != 34 * 11 * len(methods):
        raise RuntimeError("oracle-reading matrix cardinality mismatch")
    resource = json.loads((WEBCRAFT / "config/resource_model.json").read_text())
    mineable = set(resource["resource_block_counts"])
    normalizer, _ = _compiler(WEBCRAFT)
    bundles: dict[tuple[str, str], dict[str, Any]] = {}
    evidence_dir = OUTPUT / "evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    for job in jobs:
        key = (job.condition, job.task_id)
        if key in bundles:
            continue
        evidence_path = evidence_dir / f"{job.condition}__{job.task_id}.json"
        if evidence_path.is_file():
            bundle = json.loads(evidence_path.read_text())
            if bundle.get("claim_source") != "evaluator_gold_projection":
                raise RuntimeError("existing oracle-reading evidence has wrong provenance")
        else:
            bundle = selected_gold_bundle(
                WEBCRAFT, job.condition, requirements_from_task(raw_tasks[job.task_id]),
                claim_matches, mineable,
            )
            evidence_path.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
        bundles[key] = bundle
    episodes_path = OUTPUT / "episodes.jsonl"
    completed = set()
    if episodes_path.exists():
        completed = {(row["condition"], row["task_id"], row["world"], row["method"], row["seed"])
                     for row in _jsonl(episodes_path)}
    vectors = load_conflict_vectors(WEBCRAFT / "data/conflicts.jsonl")
    task_map = {row["id"]: row for row in public_tasks}
    for job in jobs:
        key = (job.condition, job.task_id, job.world, job.method, job.seed)
        if key in completed:
            continue
        record = run_episode(
            job, task_map[job.task_id], bundles[(job.condition, job.task_id)],
            root=WEBCRAFT, pin_path=ROOT / "config/webcraft_pin.json",
            index_dir=str(WEBCRAFT / "corpus/indexes/formal_p1"), output_dir=OUTPUT,
            conflict_vectors=vectors, preview=False, prior_bundle=prior,
        )
        if any(event["cost"].get("model_calls", 0) for event in record["events"]):
            raise RuntimeError("oracle-reading issued an LLM call")
        with episodes_path.open("a") as handle:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    raw_records = _jsonl(episodes_path)
    if len(raw_records) != len(jobs):
        raise RuntimeError("oracle-reading did not complete every frozen episode")
    records = [EpisodeRecord.from_dict(row) for row in raw_records]
    REPORTS.mkdir(parents=True, exist_ok=True)
    _write_csv(REPORTS / "oracle_reading_main_5_conditions.csv", aggregate_main_table(_five_conditions(records), iterations=2000, seed=20261005))
    _write_csv(REPORTS / "oracle_reading_11_cells.csv", world_breakdown(records))
    one_sided = coverage_conflict_ids(WEBCRAFT / "reports/corpus_stats/coverage_p1.csv", "one_sided")
    subset_rows = []
    for subset, selected in result_subsets_with_control(records, one_sided).items():
        for row in aggregate_main_table(selected, iterations=2000, seed=20261005) if selected else []:
            subset_rows.append({"subset": subset, **row})
    _write_csv(REPORTS / "oracle_reading_subsets.csv", subset_rows)
    closure = _closure_rows(raw_tasks, bundles, normalizer, mineable, requirements_from_task, claim_matches)
    _write_csv(REPORTS / "oracle_reading_closure.csv", closure)
    two_sided_tasks = {row.task_id for row in result_subsets_with_control(records, one_sided)["two_sided"]}
    summary = {
        "schema_version": 1, "status": "POST_REVIEW_SUPPLEMENT", "reading_mode": "oracle_reading",
        "episodes": len(raw_records), "methods": sorted({row["method"] for row in raw_records}),
        "cells": len({(row["condition"], row["world"]) for row in raw_records}),
        "seed": SEED, "model_calls": sum(event["cost"].get("model_calls", 0)
                                          for row in raw_records for event in row["events"]),
        "prior_sha256": prior_sha, "two_sided_tasks": len(two_sided_tasks),
        "closure_required": sum(row["required_mechanisms"] for row in closure),
        "closure_supplied": sum(row["supplied_mechanisms"] for row in closure),
        "closure_compiled": sum(row["compiled_mechanisms"] for row in closure),
        "episodes_sha256": hashlib.sha256(episodes_path.read_bytes()).hexdigest(),
    }
    (REPORTS / "oracle_reading_summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
