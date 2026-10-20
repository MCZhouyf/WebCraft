"""Frozen paired confirmatory comparisons for the main experiment."""
from __future__ import annotations

from collections import defaultdict
from typing import Any, Iterable

import numpy as np

from .reporting import EpisodeRecord


PAIR_KEY_FIELDS = ("condition", "world", "task_id", "seed")


def _key(row: EpisodeRecord) -> tuple[str, str, str, int]:
    return row.condition, row.world, row.task_id, row.seed


def _paired_rows(
    records: Iterable[EpisodeRecord], left: str, right: str,
) -> list[tuple[EpisodeRecord, EpisodeRecord]]:
    selected: dict[str, dict[tuple[str, str, str, int], EpisodeRecord]] = {
        left: {}, right: {},
    }
    for row in records:
        if row.method not in selected:
            continue
        key = _key(row)
        if key in selected[row.method]:
            raise ValueError(f"duplicate episode for {row.method}: {key}")
        selected[row.method][key] = row
    if set(selected[left]) != set(selected[right]) or not selected[left]:
        raise ValueError(f"{left} and {right} do not have identical nonempty episode keys")
    return [(selected[left][key], selected[right][key]) for key in sorted(selected[left])]


def _pair_clusters(
    pairs: list[tuple[EpisodeRecord, EpisodeRecord]],
) -> list[list[tuple[EpisodeRecord, EpisodeRecord]]]:
    parent = list(range(len(pairs)))

    def find(index: int) -> int:
        while parent[index] != index:
            parent[index] = parent[parent[index]]
            index = parent[index]
        return index

    def union(left: int, right: int) -> None:
        left_root, right_root = find(left), find(right)
        if left_root != right_root:
            parent[right_root] = left_root

    owners: dict[str, int] = {}
    for index, (left, right) in enumerate(pairs):
        if left.conflict_mechanism_ids != right.conflict_mechanism_ids:
            raise ValueError(f"paired conflict IDs differ for {_key(left)}")
        mechanisms = left.conflict_mechanism_ids or ("control",)
        for mechanism in mechanisms:
            if mechanism in owners:
                union(index, owners[mechanism])
            else:
                owners[mechanism] = index
    grouped: dict[int, list[tuple[EpisodeRecord, EpisodeRecord]]] = defaultdict(list)
    for index, pair in enumerate(pairs):
        grouped[find(index)].append(pair)
    return list(grouped.values())


def paired_cluster_difference(
    records: Iterable[EpisodeRecord], left: str, right: str, *,
    attribute: str = "committed_htr", iterations: int = 10_000,
    seed: int = 20_261_005,
) -> dict[str, Any]:
    """Return left-minus-right difference with a mechanism-cluster interval."""
    if iterations != 10_000 or seed != 20_261_005:
        raise ValueError("formal paired bootstrap is frozen at 10000/20261005")
    pairs = _paired_rows(records, left, right)
    clusters = _pair_clusters(pairs)

    def difference(pair: tuple[EpisodeRecord, EpisodeRecord]) -> float:
        return float(getattr(pair[0], attribute) - getattr(pair[1], attribute))

    estimate = float(np.mean([difference(pair) for pair in pairs]))
    rng = np.random.default_rng(seed)
    draws = []
    for _ in range(iterations):
        indexes = rng.integers(0, len(clusters), size=len(clusters))
        sampled = [pair for index in indexes for pair in clusters[int(index)]]
        draws.append(float(np.mean([difference(pair) for pair in sampled])))
    lower, upper = np.quantile(draws, [0.025, 0.975])
    return {
        "left": left,
        "right": right,
        "metric": attribute,
        "difference": estimate,
        "ci_low": float(lower),
        "ci_high": float(upper),
        "episodes": len(pairs),
        "mechanism_clusters": len(clusters),
        "bootstrap_iterations": iterations,
        "bootstrap_seed": seed,
    }


def confirmatory_decision(records: Iterable[EpisodeRecord]) -> dict[str, Any]:
    rows = list(records)
    naive_htr = paired_cluster_difference(rows, "naive_rag", "arbiter")
    shell_htr = paired_cluster_difference(rows, "arbiter_shell_naive", "arbiter")
    naive_sr = paired_cluster_difference(rows, "arbiter", "naive_rag", attribute="sr")
    primary_i = bool(naive_htr["difference"] >= 0.15 and naive_sr["difference"] >= 0)
    primary_ii = bool(
        shell_htr["difference"] > 0
        and (shell_htr["ci_low"] > 0 or shell_htr["ci_high"] < 0)
    )
    outcome = "both" if primary_i and primary_ii else "only_i" if primary_i else "i_failed"
    return {
        "schema_version": 1,
        "primary_i": primary_i,
        "primary_ii": primary_ii,
        "outcome": outcome,
        "naive_minus_arbiter_HTR_commit": naive_htr,
        "arbiter_minus_naive_SR": naive_sr,
        "shell_minus_arbiter_HTR_commit": shell_htr,
    }
