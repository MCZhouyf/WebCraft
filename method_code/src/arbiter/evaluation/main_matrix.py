"""Frozen main-experiment grid construction without loading evaluation records."""
from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path
from typing import Iterable

from .p6_matrix import P6Job


MAIN_CELLS = (
    ("matched_1.16.5", "1.16.5"),
    ("matched_1.19", "1.19"),
    ("matched_1.21.11", "1.21.11"),
    ("mismatched_old2new", "1.21.11"),
    ("mismatched_new2old", "1.16.5"),
    ("mixed", "1.16.5"),
    ("mixed", "1.19"),
    ("mixed", "1.21.11"),
    ("polluted", "1.16.5"),
    ("polluted", "1.19"),
    ("polluted", "1.21.11"),
)
READING_MODES = ("all_public", "llm_only")
SEEDS = (20261006, 20261007)
BASELINES = (
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "oracle",
)
METHOD_SETS = {
    "baselines_only": BASELINES,
    "baselines_plus_arbiter": BASELINES + ("arbiter",),
}
DEVELOPMENT_IDS = {
    "eff_007", "fea_002", "fea_009", "pla_001", "pla_003",
    "pla_008", "pla_009", "pla_011",
}


def build_main_matrix(
    task_ids: Iterable[str], method_set: str, *,
    cells: Iterable[tuple[str, str]] = MAIN_CELLS,
) -> list[P6Job]:
    ids = sorted(map(str, task_ids))
    if len(ids) != 34 or len(set(ids)) != 34:
        raise ValueError("main experiment requires exactly 34 distinct evaluation tasks")
    leaked = sorted(set(ids) & DEVELOPMENT_IDS)
    if leaked:
        raise ValueError(f"development tasks entered main matrix: {leaked}")
    try:
        methods = METHOD_SETS[method_set]
    except KeyError as exc:
        raise ValueError(f"unknown main method set: {method_set}") from exc
    selected_cells = tuple(cells)
    if not selected_cells or any(cell not in MAIN_CELLS for cell in selected_cells):
        raise ValueError("main matrix contains an unregistered world-condition cell")
    jobs = [
        P6Job(condition, task_id, world, method, seed)
        for condition, world in selected_cells
        for task_id in ids
        for method in methods
        for seed in SEEDS
    ]
    expected = len(selected_cells) * 34 * len(methods) * len(SEEDS)
    if len(jobs) != expected:
        raise AssertionError("main matrix cardinality changed")
    return jobs


def write_main_manifest(
    jobs: list[P6Job], path: Path, *, method_set: str, reading_mode: str,
) -> None:
    if reading_mode not in READING_MODES:
        raise ValueError("unknown reading mode")
    cells = sorted({(job.condition, job.world) for job in jobs})
    payload = {
        "schema_version": 1, "method_set": method_set,
        "reading_mode": reading_mode, "cells": [list(cell) for cell in cells],
        "seeds": list(SEEDS), "episodes": len(jobs),
        "jobs": [asdict(job) for job in jobs],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
