"""Pre-registered tables from schema-v2 deterministic-backend episodes."""
from __future__ import annotations

import csv
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import numpy as np


@dataclass(frozen=True)
class EpisodeRecord:
    method: str
    condition: str
    world: str
    task_id: str
    seed: int
    conflict_mechanism_ids: tuple[str, ...]
    sr: float
    htr: float
    probes: float
    searches: float = 0.0
    htr_commit: float | None = None
    probe_cost: float = 0.0

    @property
    def htr_all(self) -> float:
        return self.htr

    @property
    def committed_htr(self) -> float:
        return self.htr if self.htr_commit is None else self.htr_commit

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> "EpisodeRecord":
        required = {
            "schema_version", "record_type", "method", "condition", "world",
            "task_id", "seed", "conflict_mechanism_ids", "events", "metrics",
            "probe_count",
        }
        if not required <= set(row) or row["schema_version"] != 2 or row["record_type"] != "episode":
            raise ValueError("episode record does not satisfy the schema-v2 summary contract")
        events = row["events"]
        if not isinstance(events, list) or any(
            not isinstance(event, dict) or event.get("schema_version") != 2
            or not isinstance(event.get("cost"), dict) or "primary" not in event["cost"]
            for event in events
        ):
            raise ValueError("episode contains a non-schema-v2 action event")
        metrics = row["metrics"]
        if (not isinstance(metrics, dict) or "SR" not in metrics
                or not ({"HTR", "HTR_all"} & set(metrics))):
            raise ValueError("episode metrics must contain SR and HTR_all (or legacy HTR)")
        raw_conflicts = row["conflict_mechanism_ids"]
        if not isinstance(raw_conflicts, list):
            raise ValueError("episode conflict mechanism IDs must be a list")
        conflicts = tuple(sorted(set(raw_conflicts)))
        if any(not isinstance(value, str) or not value.startswith("cf_")
               for value in conflicts):
            raise ValueError("episode contains invalid conflict mechanism IDs")
        return cls(
            method=str(row["method"]), condition=str(row["condition"]),
            world=str(row["world"]), task_id=str(row["task_id"]),
            seed=int(row["seed"]), conflict_mechanism_ids=conflicts,
            sr=float(metrics["SR"]),
            htr=float(metrics.get("HTR_all", metrics.get("HTR"))),
            probes=float(row["probe_count"]),
            searches=float(row.get("reader", {}).get("search_count", 0)),
            htr_commit=float(metrics.get(
                "HTR_commit", metrics.get("HTR_all", metrics.get("HTR")),
            )),
            probe_cost=float(row.get("probe_cost", metrics.get("probe_cost", 0))),
        )


def load_episode_records(path: Path) -> list[EpisodeRecord]:
    files = sorted(path.rglob("*.jsonl")) if path.is_dir() else [path]
    records = []
    for file in files:
        for line_number, line in enumerate(file.read_text().splitlines(), 1):
            if not line.strip():
                continue
            raw = json.loads(line)
            if raw.get("record_type") != "episode":
                continue
            try:
                records.append(EpisodeRecord.from_dict(raw))
            except (TypeError, ValueError) as exc:
                raise ValueError(f"{file}:{line_number}: {exc}") from exc
    if not records:
        raise ValueError("no schema-v2 episode summary records found")
    return records


def validate_conflicts(records: Iterable[EpisodeRecord], catalog_path: Path) -> None:
    known = {
        json.loads(line)["id"] for line in catalog_path.read_text().splitlines() if line.strip()
    }
    unknown = sorted({
        mechanism for row in records for mechanism in row.conflict_mechanism_ids
        if mechanism not in known
    })
    if unknown:
        raise ValueError(f"episode records contain unknown conflict IDs: {unknown}")


def _components(rows: list[EpisodeRecord]) -> list[list[EpisodeRecord]]:
    parent = list(range(len(rows)))

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
    for index, row in enumerate(rows):
        # The frozen subset rule assigns tasks without an active grade-A
        # conflict to one fixed control cluster.
        mechanisms = row.conflict_mechanism_ids or ("control",)
        for mechanism in mechanisms:
            if mechanism in owners:
                union(index, owners[mechanism])
            else:
                owners[mechanism] = index
    groups: dict[int, list[EpisodeRecord]] = defaultdict(list)
    for index, row in enumerate(rows):
        groups[find(index)].append(row)
    return list(groups.values())


def _cluster_interval(
    rows: list[EpisodeRecord], attribute: str, rng: np.random.Generator,
    iterations: int,
) -> tuple[float, float, float, int]:
    values = np.array([getattr(row, attribute) for row in rows], dtype=float)
    clusters = _components(rows)
    estimates = []
    for _ in range(iterations):
        sampled = rng.integers(0, len(clusters), size=len(clusters))
        draw = [row for index in sampled for row in clusters[int(index)]]
        estimates.append(float(np.mean([getattr(row, attribute) for row in draw])))
    lower, upper = np.quantile(estimates, [0.025, 0.975])
    return float(values.mean()), float(lower), float(upper), len(clusters)


def aggregate_main_table(
    records: Iterable[EpisodeRecord], *, iterations: int = 2000, seed: int = 2026,
) -> list[dict[str, Any]]:
    if iterations < 100:
        raise ValueError("cluster bootstrap requires at least 100 iterations")
    grouped: dict[tuple[str, str], list[EpisodeRecord]] = defaultdict(list)
    for row in records:
        grouped[(row.method, row.condition)].append(row)
    rng = np.random.default_rng(seed)
    output = []
    for (method, condition), rows in sorted(grouped.items()):
        result: dict[str, Any] = {
            "method": method, "condition": condition, "episodes": len(rows),
            "worlds": len({row.world for row in rows}),
        }
        cluster_counts = set()
        for label, attribute in (
            ("SR", "sr"), ("HTR_commit", "committed_htr"),
            ("HTR_all", "htr_all"), ("probe_count", "probes"),
            ("probe_cost", "probe_cost"), ("searches", "searches"),
        ):
            mean, lower, upper, clusters = _cluster_interval(
                rows, attribute, rng, iterations,
            )
            result[label] = mean
            result[f"{label}_ci_low"] = lower
            result[f"{label}_ci_high"] = upper
            cluster_counts.add(clusters)
        result["mechanism_clusters"] = cluster_counts.pop()
        output.append(result)
    return output


def world_breakdown(records: Iterable[EpisodeRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[EpisodeRecord]] = defaultdict(list)
    for row in records:
        grouped[(row.method, row.condition, row.world)].append(row)
    return [
        {
            "method": key[0], "condition": key[1], "world": key[2],
            "episodes": len(rows), "SR": float(np.mean([row.sr for row in rows])),
            "HTR_commit": float(np.mean([row.committed_htr for row in rows])),
            "HTR_all": float(np.mean([row.htr_all for row in rows])),
            "probe_count": float(np.mean([row.probes for row in rows])),
            "probe_cost": float(np.mean([row.probe_cost for row in rows])),
            "searches": float(np.mean([row.searches for row in rows])),
        }
        for key, rows in sorted(grouped.items())
    ]


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError("cannot write an empty table")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _latex(rows: list[dict[str, Any]]) -> str:
    conditions = sorted({str(row["condition"]) for row in rows})
    methods = sorted({str(row["method"]) for row in rows})
    lookup = {(row["method"], row["condition"]): row for row in rows}
    lines = [
        "\\begin{tabular}{l" + "c" * len(conditions) + "}",
        "Method & " + " & ".join(value.replace("_", "\\_") for value in conditions) + " \\\\",
        "\\hline",
    ]
    for method in methods:
        cells = []
        for condition in conditions:
            row = lookup.get((method, condition))
            cells.append("--" if row is None else (
                f"{row['SR']:.3f} [{row['SR_ci_low']:.3f}, {row['SR_ci_high']:.3f}] / "
                f"{row['HTR_commit']:.3f} [{row['HTR_commit_ci_low']:.3f}, {row['HTR_commit_ci_high']:.3f}] / "
                f"{row['HTR_all']:.3f} [{row['HTR_all_ci_low']:.3f}, {row['HTR_all_ci_high']:.3f}] / "
                f"{row['probe_count']:.2f} [{row['probe_count_ci_low']:.2f}, "
                f"{row['probe_count_ci_high']:.2f}] / "
                f"{row['probe_cost']:.2f} [{row['probe_cost_ci_low']:.2f}, "
                f"{row['probe_cost_ci_high']:.2f}] / "
                f"{row['searches']:.2f} [{row['searches_ci_low']:.2f}, "
                f"{row['searches_ci_high']:.2f}]"
            ))
        lines.append(method.replace("_", "\\_") + " & " + " & ".join(cells) + " \\\\")
    lines.extend(["\\hline", "\\multicolumn{" + str(len(conditions) + 1)
                  + "}{l}{Cells: SR / HTR_commit / HTR_all / mean probe count / mean probe cost / mean searches (all with 95\\% CI)}",
                  "\\end{tabular}"])
    return "\n".join(lines) + "\n"


def write_main_table(
    records: list[EpisodeRecord], output_dir: Path, *, iterations: int = 2000,
    seed: int = 2026,
) -> list[dict[str, Any]]:
    output_dir.mkdir(parents=True, exist_ok=True)
    rows = aggregate_main_table(records, iterations=iterations, seed=seed)
    _write_csv(output_dir / "main_table.csv", rows)
    _write_csv(output_dir / "world_breakdown.csv", world_breakdown(records))
    (output_dir / "main_table.tex").write_text(_latex(rows))
    return rows
