"""Frozen P6 development matrix and author-defined stop rule."""
from __future__ import annotations

import json
import re
import csv
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable

import numpy as np

from .reporting import EpisodeRecord


P6_CONDITIONS = ("mixed", "mismatched_old2new", "mismatched_new2old")
P6_WORLDS = ("1.16.5", "1.19", "1.21.11")
P6_METHODS = ("naive_rag", "arbiter")
P6_SEEDS = (2026,)


@dataclass(frozen=True)
class P6Job:
    condition: str
    task_id: str
    world: str
    method: str
    seed: int


def development_tasks(snapshot_path: Path, resource_model_path: Path) -> list[dict]:
    tasks = json.loads(snapshot_path.read_text())
    if not isinstance(tasks, list):
        raise ValueError("P6 development snapshot must be a JSON list")
    selected = sorted(tasks, key=lambda row: row["id"])
    expected_ids = {
        "eff_007", "fea_002", "fea_009", "pla_001", "pla_003",
        "pla_008", "pla_009", "pla_011",
    }
    if {task["id"] for task in selected} != expected_ids or len(selected) != 8:
        raise ValueError("P6 snapshot must contain exactly the eight frozen development tasks")
    resource_model = json.loads(resource_model_path.read_text())
    standard_items = resource_model["standard_inventory"]
    for task in selected:
        task["standard_items"] = standard_items
        task["task_supplements"] = []
    return selected


def build_matrix(tasks: Iterable[dict]) -> list[P6Job]:
    task_ids = [str(task["id"]) for task in tasks]
    if len(task_ids) != 8 or len(set(task_ids)) != 8:
        raise ValueError("P6 matrix requires 8 distinct task IDs")
    jobs = [
        P6Job(condition, task_id, world, method, seed)
        for condition in P6_CONDITIONS
        for task_id in sorted(task_ids)
        for world in P6_WORLDS
        for method in P6_METHODS
        for seed in P6_SEEDS
    ]
    if len(jobs) != 144:
        raise AssertionError("P6 matrix cardinality changed")
    return jobs


def write_manifest(jobs: list[P6Job], path: Path) -> None:
    payload = {
        "schema_version": 1,
        "conditions": list(P6_CONDITIONS), "worlds": list(P6_WORLDS),
        "methods": list(P6_METHODS), "seeds": list(P6_SEEDS),
        "episodes": len(jobs), "jobs": [asdict(job) for job in jobs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")


def validate_coverage(jobs: Iterable[P6Job], records: Iterable[EpisodeRecord]) -> None:
    expected = {
        (job.condition, job.task_id, job.world, job.method, job.seed) for job in jobs
    }
    actual_rows = [
        (row.condition, row.task_id, row.world, row.method, row.seed) for row in records
    ]
    actual = set(actual_rows)
    if len(actual_rows) != len(actual):
        raise ValueError("P6 results contain duplicate matrix cells")
    missing, extra = sorted(expected - actual), sorted(actual - expected)
    if missing or extra:
        raise ValueError(f"P6 coverage mismatch: missing={len(missing)}, extra={len(extra)}")


def one_sided_conflict_ids(freeze_path: Path) -> frozenset[str]:
    text = freeze_path.read_text()
    match = re.search(
        r"The\s+`one_sided`\s+subset\s+is:\s*(.*?)(?:\n##\s|\Z)",
        text,
        re.DOTALL,
    )
    if match is None:
        raise ValueError("corpus freeze does not declare the one_sided subset")
    values = frozenset(re.findall(r"`(cf_[0-9a-f]{16})`", match.group(1)))
    if not values:
        raise ValueError("corpus freeze declares an empty one_sided subset")
    return values


def coverage_conflict_ids(path: Path, label: str) -> frozenset[str]:
    """Read a frozen P1 coverage class without inferring it from prose history."""
    if label not in {"two_sided", "one_sided"}:
        raise ValueError("unsupported coverage class")
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    required = {"conflict_id", "coverage"}
    if not rows or not required <= set(rows[0]):
        raise ValueError("coverage table lacks conflict_id/coverage columns")
    if any(row["coverage"] not in {"two_sided", "one_sided"} for row in rows):
        raise ValueError("coverage table contains an unknown class")
    values = frozenset(
        row["conflict_id"] for row in rows if row["coverage"] == label
    )
    if not values or any(not value.startswith("cf_") for value in values):
        raise ValueError(f"coverage table has invalid or empty {label} IDs")
    return values


def result_subsets(
    records: Iterable[EpisodeRecord], one_sided: frozenset[str],
) -> dict[str, list[EpisodeRecord]]:
    rows = list(records)
    one = [row for row in rows if one_sided.intersection(row.conflict_mechanism_ids)]
    two = [row for row in rows if not one_sided.intersection(row.conflict_mechanism_ids)]
    if len(one) + len(two) != len(rows):
        raise AssertionError("P6 subset partition is not exhaustive")
    return {"all": rows, "two_sided": two, "one_sided": one}


def result_subsets_with_control(
    records: Iterable[EpisodeRecord], one_sided: frozenset[str],
) -> dict[str, list[EpisodeRecord]]:
    """Partition conflict-bearing tasks by coverage and keep controls separate."""
    rows = list(records)
    control = [row for row in rows if not row.conflict_mechanism_ids]
    one = [
        row for row in rows
        if row.conflict_mechanism_ids
        and one_sided.intersection(row.conflict_mechanism_ids)
    ]
    two = [
        row for row in rows
        if row.conflict_mechanism_ids
        and not one_sided.intersection(row.conflict_mechanism_ids)
    ]
    if len(control) + len(one) + len(two) != len(rows):
        raise AssertionError("corrected subset partition is not exhaustive")
    return {
        "all": rows, "two_sided": two,
        "one_sided": one, "control": control,
    }


def judge_gate(records: Iterable[EpisodeRecord]) -> dict[str, float | str | bool | int]:
    rows = list(records)
    grouped = {method: [row for row in rows if row.method == method] for method in P6_METHODS}
    if any(len(grouped[method]) != 72 for method in P6_METHODS):
        raise ValueError("P6 gate requires 72 episodes for each method")
    naive_htr_all = float(np.mean([row.htr_all for row in grouped["naive_rag"]]))
    arbiter_htr_all = float(np.mean([row.htr_all for row in grouped["arbiter"]]))
    naive_htr_commit = float(np.mean([row.committed_htr for row in grouped["naive_rag"]]))
    arbiter_htr_commit = float(np.mean([row.committed_htr for row in grouped["arbiter"]]))
    naive_sr = float(np.mean([row.sr for row in grouped["naive_rag"]]))
    arbiter_sr = float(np.mean([row.sr for row in grouped["arbiter"]]))
    probes = float(np.mean([row.probes for row in grouped["arbiter"]]))
    probe_cost = float(np.mean([row.probe_cost for row in grouped["arbiter"]]))
    gap_commit = naive_htr_commit - arbiter_htr_commit
    gap_all = naive_htr_all - arbiter_htr_all
    sr_not_lower = arbiter_sr >= naive_sr

    def decision(gap: float) -> str:
        if gap >= 0.15 and sr_not_lower:
            return "PASS"
        if gap >= 0.05 and sr_not_lower:
            return "MARGINAL"
        return "FAIL"

    return {
        "decision": decision(gap_commit), "HTR_all_decision": decision(gap_all),
        "episodes": len(rows),
        "naive_rag_HTR_commit": naive_htr_commit,
        "arbiter_HTR_commit": arbiter_htr_commit,
        "HTR_commit_gap": gap_commit,
        "naive_rag_HTR_all": naive_htr_all,
        "arbiter_HTR_all": arbiter_htr_all,
        "HTR_all_gap": gap_all,
        "naive_rag_SR": naive_sr, "arbiter_SR": arbiter_sr,
        "SR_not_lower": sr_not_lower, "arbiter_mean_probes": probes,
        "arbiter_mean_probe_cost": probe_cost,
        "pass_HTR_commit_gap": gap_commit >= 0.15,
        "pass_HTR_all_gap": gap_all >= 0.15,
    }
