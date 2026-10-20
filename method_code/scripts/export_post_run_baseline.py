"""Append the frozen post-run baseline and correct the vacuous subset split."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from arbiter.evaluation.p6_matrix import (
    coverage_conflict_ids,
    result_subsets_with_control,
)
from arbiter.evaluation.reporting import (
    EpisodeRecord,
    _components,
    load_episode_records,
)


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
EXPORTS = ROOT / "reports/main/acceptance_exports"
MAIN = ROOT / "artifacts/main"
POST = ROOT / "artifacts/post_run_probe_then_rag"
MODES = ("all_public", "llm_only")
SEEDS = (20261006, 20261007)
METHOD = "probe_then_rag"
STATUS_MAIN = "p7_frozen_main"
STATUS_POST = "post_run_baseline_addition"
ITERATIONS = 10_000
BOOTSTRAP_SEED = 20261005
MECHANISM_ACTIONS = {"mine", "craft_grid", "smelt", "check_name"}
CONDITION_GROUPS = {
    "matched_1.16.5": "matched",
    "matched_1.19": "matched",
    "matched_1.21.11": "matched",
    "mismatched_old2new": "mismatched_old2new",
    "mismatched_new2old": "mismatched_new2old",
    "mixed": "mixed",
    "polluted": "polluted",
}
COMPARISONS = (
    ("matched_minus_mismatched_old2new", "matched", "mismatched_old2new"),
    ("matched_minus_mismatched_new2old", "matched", "mismatched_new2old"),
    ("matched_minus_mixed", "matched", "mixed"),
    ("mixed_minus_polluted", "mixed", "polluted"),
)


def _jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def _write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"cannot write empty table: {path}")
    fields = list(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise ValueError(f"inconsistent columns for {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _markdown(rows: list[dict[str, Any]]) -> str:
    fields = list(rows[0])
    lines = [
        "| " + " | ".join(fields) + " |",
        "| " + " | ".join("---" for _ in fields) + " |",
    ]
    for row in rows:
        lines.append("| " + " | ".join(
            "" if row[field] is None else str(row[field]) for field in fields
        ) + " |")
    return "\n".join(lines) + "\n"


def _write_pair(stem: str, rows: list[dict[str, Any]]) -> None:
    _write_csv(EXPORTS / f"{stem}.csv", rows)
    (EXPORTS / f"{stem}.md").write_text(_markdown(rows))


def _status(method: str) -> str:
    return STATUS_POST if method == METHOD else STATUS_MAIN


def _append_rows(stem: str, additions: list[dict[str, Any]]) -> None:
    rows: list[dict[str, Any]] = []
    for row in _read_csv(EXPORTS / f"{stem}.csv"):
        if row.get("method") == METHOD:
            continue
        rows.append({**row, "analysis_status": _status(row.get("method", ""))})
    rows.extend({**row, "analysis_status": STATUS_POST} for row in additions)
    _write_pair(stem, rows)


def _aggregate(records: list[EpisodeRecord]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[EpisodeRecord]] = defaultdict(list)
    for row in records:
        grouped[(row.method, row.condition)].append(row)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
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
            mean, low, high, clusters = _fast_cluster_interval(
                rows, attribute, rng,
            )
            result[label] = mean
            result[f"{label}_ci_low"] = low
            result[f"{label}_ci_high"] = high
            cluster_counts.add(clusters)
        result["mechanism_clusters"] = cluster_counts.pop()
        output.append(result)
    return output


def _fast_cluster_interval(
    rows: list[EpisodeRecord], attribute: str, rng: np.random.Generator,
) -> tuple[float, float, float, int]:
    clusters = _components(rows)
    sums = np.asarray([
        sum(float(getattr(row, attribute)) for row in cluster)
        for cluster in clusters
    ])
    counts = np.asarray([len(cluster) for cluster in clusters])
    sampled = rng.integers(
        0, len(clusters), size=(ITERATIONS, len(clusters)),
    )
    estimates = sums[sampled].sum(axis=1) / counts[sampled].sum(axis=1)
    low, high = np.quantile(estimates, [0.025, 0.975])
    mean = float(np.mean([float(getattr(row, attribute)) for row in rows]))
    return mean, float(low), float(high), len(clusters)


def _baseline_main_tables(mode: str, records: list[EpisodeRecord]) -> None:
    grouped = [
        replace(row, condition=CONDITION_GROUPS[row.condition]) for row in records
    ]
    _append_rows(f"{mode}_main_5_conditions", _aggregate(grouped))

    cells = []
    world_for_condition = {(row.condition, row.world) for row in records}
    if len(world_for_condition) != 11:
        raise ValueError("post-run baseline does not cover 11 cells")
    keyed = [
        replace(row, condition=f"{row.condition}@@{row.world}") for row in records
    ]
    for row in _aggregate(keyed):
        row = dict(row)
        row.pop("worlds")
        row["condition"], row["world"] = row["condition"].split("@@", 1)
        fields = list(row)
        fields.insert(2, fields.pop(fields.index("world")))
        cells.append({field: row[field] for field in fields})
    _append_rows(f"{mode}_11_cells", cells)


def _all_records(mode: str) -> list[EpisodeRecord]:
    return (
        load_episode_records(MAIN / mode / "episodes.jsonl")
        + load_episode_records(POST / mode / "episodes.jsonl")
    )


def _corrected_subsets(mode: str, records: list[EpisodeRecord]) -> list[dict[str, Any]]:
    one_sided = coverage_conflict_ids(
        WEBCRAFT / "reports/corpus_stats/coverage_p1.csv", "one_sided",
    )
    subsets = result_subsets_with_control(records, one_sided)
    methods = sorted({row.method for row in records})
    output = []
    for subset in ("all", "two_sided", "one_sided", "control"):
        selected = subsets[subset]
        available = {row.method for row in selected}
        aggregated = {
            row["method"]: row for row in _aggregate([
                replace(record, condition=subset) for record in selected
            ])
        } if selected else {}
        for method in methods:
            if method in available:
                row = dict(aggregated[method])
                row["subset"] = row.pop("condition")
            else:
                row = {
                    "method": method, "episodes": 0, "worlds": 0,
                    **{
                        name: "" for metric in (
                            "SR", "HTR_commit", "HTR_all", "probe_count",
                            "probe_cost", "searches",
                        ) for name in (metric, f"{metric}_ci_low", f"{metric}_ci_high")
                    },
                    "mechanism_clusters": 0, "subset": subset,
                }
            fields = list(row)
            fields.insert(0, fields.pop(fields.index("subset")))
            normalized = {field: row[field] for field in fields}
            normalized["analysis_status"] = _status(method)
            output.append(normalized)
    return output


def _subset_membership(records: list[EpisodeRecord]) -> list[dict[str, Any]]:
    one_sided = coverage_conflict_ids(
        WEBCRAFT / "reports/corpus_stats/coverage_p1.csv", "one_sided",
    )
    task_conflicts: dict[str, tuple[str, ...]] = {}
    for row in records:
        current = task_conflicts.setdefault(row.task_id, row.conflict_mechanism_ids)
        if current != row.conflict_mechanism_ids:
            raise ValueError(f"task conflict set changed: {row.task_id}")
    output = []
    for task_id, conflicts in sorted(task_conflicts.items()):
        subset = (
            "control" if not conflicts
            else "one_sided" if one_sided.intersection(conflicts)
            else "two_sided"
        )
        output.append({
            "subset": subset, "task_id": task_id,
            "conflict_mechanism_ids": ";".join(conflicts),
        })
    return output


def _paired_rows(mode: str, raw: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [row for row in raw if row["method"] == METHOD]
    indexed = {
        (row["condition"], row["world"], row["task_id"], int(row["seed"])): row
        for row in rows
    }
    output = []
    for name, left_group, right_group in COMPARISONS:
        paired: list[tuple[dict[str, Any], dict[str, Any]]] = []
        for right in rows:
            if CONDITION_GROUPS[right["condition"]] != right_group:
                continue
            left_condition = (
                f"matched_{right['world']}" if left_group == "matched" else left_group
            )
            left = indexed[(
                left_condition, right["world"], right["task_id"], int(right["seed"]),
            )]
            paired.append((left, right))
        for metric, attribute in (("SR", "sr"), ("HTR_commit", "committed_htr")):
            synthetic = []
            for left, right in paired:
                difference = float(left["metrics"][metric]) - float(right["metrics"][metric])
                synthetic.append(EpisodeRecord(
                    method=METHOD, condition=name, world=right["world"],
                    task_id=right["task_id"], seed=int(right["seed"]),
                    conflict_mechanism_ids=tuple(right["conflict_mechanism_ids"]),
                    sr=difference, htr=difference, probes=0,
                    htr_commit=difference,
                ))
            mean, low, high, clusters = _fast_cluster_interval(
                synthetic, attribute, np.random.default_rng(BOOTSTRAP_SEED),
            )
            output.append({
                "reading_mode": mode, "method": METHOD, "comparison": name,
                "metric": metric, "paired_episodes": len(paired),
                "difference": mean, "ci_low": low, "ci_high": high,
                "mechanism_clusters": clusters,
                "bootstrap_iterations": ITERATIONS,
                "bootstrap_seed": BOOTSTRAP_SEED,
            })
    return output


def _terminal(row: dict[str, Any]) -> str:
    if row["metrics"]["SR"]:
        return "solved" if row["true_feasible"] else "correctly_infeasible"
    semantic = [event for event in row["events"] if event.get("method") in MECHANISM_ACTIONS]
    if row["metrics"]["primary_cost"] >= 90:
        return "budget_exhausted"
    if any(event.get("success") is False for event in semantic):
        return "execution_failed"
    if row["true_feasible"] and row["reported_feasible"] is False:
        return "plan_not_found" if not semantic else "incorrectly_infeasible"
    return "execution_failed"


def _terminal_row(mode: str, raw: list[dict[str, Any]]) -> dict[str, Any]:
    counts = Counter(_terminal(row) for row in raw)
    return {
        "reading_mode": mode, "method": METHOD, "episodes": len(raw),
        **{name: counts[name] for name in (
            "solved", "correctly_infeasible", "incorrectly_infeasible",
            "plan_not_found", "execution_failed", "budget_exhausted",
        )},
    }


def _secondary_row(mode: str, raw: list[dict[str, Any]]) -> dict[str, Any]:
    sys.path.insert(0, str(WEBCRAFT))
    from metrics.episode import aggregate, episode_metrics

    tasks = {
        row["id"]: row for row in _jsonl(WEBCRAFT / "data/tasks.jsonl")
        if row.get("split") == "eval"
    }
    conflicts = {
        row["id"]: row["truth_vector"]
        for row in _jsonl(WEBCRAFT / "data/conflicts.jsonl")
    }
    version_hashes = {
        hashlib.sha256(version.encode()).hexdigest(): version
        for version in ("1.16.5", "1.19", "1.21.11")
    }
    scored = []
    semantic_total = 0
    for row in raw:
        task = tasks[row["task_id"]]
        mechanisms = iter(row["used_mechanism_ids"])
        vectors = {}
        for index, event in enumerate(row["events"]):
            if event.get("method") not in MECHANISM_ACTIONS:
                continue
            mechanism = next(mechanisms)
            semantic_total += 1
            if mechanism in conflicts:
                vectors[index] = conflicts[mechanism]
        try:
            next(mechanisms)
        except StopIteration:
            pass
        else:
            raise ValueError("action/mechanism alignment changed")
        initial = {
            item["name"]: int(item["count"])
            for item in task["standard_items"] + task.get("task_supplements", [])
        }
        scored.append(episode_metrics(
            row["events"], goal=task["target"], world=row["world"],
            true_feasible=row["true_feasible"],
            success=bool(row["metrics"]["SR"]),
            reported_feasible=row["reported_feasible"],
            version_hashes=version_hashes, mechanism_vectors=vectors,
            oracle_cost=task["oracle_cost"][row["world"]],
            task_type=task["type"], initial_inventory=initial,
        ))
    summary = aggregate(scored)
    searches = sum(float(row["reader"]["search_count"]) for row in raw)
    return {
        "reading_mode": mode, "method": METHOD, "episodes": len(raw),
        "IC": summary["IC"], "IC_n": summary["IC_n"],
        "IAA": summary["IAA"], "IAA_n": summary["IAA_n"],
        "feasibility_precision": summary["feasibility_precision"],
        "feasibility_recall": summary["feasibility_recall"],
        "feasibility_F1": summary["feasibility_F1"],
        "false_abstention_rate": summary["false_abstention_rate"],
        "OPR": summary["OPR"], "OPR_n": summary["OPR_n"],
        "OVR": summary["OVR"], "OVR_n": summary["OVR_n"],
        "mean_semantic_actions": semantic_total / len(raw),
        "total_semantic_actions": semantic_total,
        "mean_searches": searches / len(raw), "total_searches": searches,
    }


def _seed_audit(mode: str, raw: list[dict[str, Any]]) -> dict[str, Any]:
    pairs: dict[tuple[str, str, str], dict[int, dict[str, Any]]] = defaultdict(dict)
    for row in raw:
        pairs[(row["condition"], row["world"], row["task_id"])][int(row["seed"])] = row
    identical = 0
    for pair in pairs.values():
        if set(pair) != set(SEEDS):
            raise ValueError("post-run baseline seed pair is incomplete")
        left, right = dict(pair[SEEDS[0]]), dict(pair[SEEDS[1]])
        left.pop("seed")
        right.pop("seed")
        identical += left == right
    return {
        "reading_mode": mode, "scope": METHOD, "seed_pairs": len(pairs),
        "bitwise_identical_after_removing_seed": identical,
        "nonidentical_pairs": len(pairs) - identical,
        "bootstrap_unit": "mechanism-connected cluster containing both seed rows",
        "seeds_treated_as_independent": False,
        "analysis_status": STATUS_POST,
    }


def _append_seed_audits(rows: list[dict[str, Any]]) -> None:
    existing = []
    for row in _read_csv(EXPORTS / "seed_identity.csv"):
        if row.get("scope") == METHOD:
            continue
        existing.append({
            "reading_mode": row["reading_mode"],
            "scope": row.get("scope") or "nine_method_main",
            "seed_pairs": row["seed_pairs"],
            "bitwise_identical_after_removing_seed": row["bitwise_identical_after_removing_seed"],
            "nonidentical_pairs": row["nonidentical_pairs"],
            "bootstrap_unit": row["bootstrap_unit"],
            "seeds_treated_as_independent": row["seeds_treated_as_independent"],
            "analysis_status": row.get("analysis_status") or STATUS_MAIN,
        })
    _write_pair("seed_identity", existing + rows)


def main() -> None:
    EXPORTS.mkdir(parents=True, exist_ok=True)
    paired_additions = []
    terminal_additions = []
    secondary_additions = []
    seed_additions = []
    audit = {"schema_version": 1, "method": METHOD, "modes": {}}
    membership_written = False
    for mode in MODES:
        raw = _jsonl(POST / mode / "episodes.jsonl")
        records = load_episode_records(POST / mode / "episodes.jsonl")
        if len(raw) != 748 or len(records) != 748:
            raise ValueError(f"incomplete {mode} post-run baseline")
        _baseline_main_tables(mode, records)
        all_records = _all_records(mode)
        _write_pair(f"{mode}_subsets_corrected", _corrected_subsets(mode, all_records))
        if not membership_written:
            _write_pair("subset_task_membership", _subset_membership(all_records))
            membership_written = True
        paired_additions.extend(_paired_rows(mode, raw))
        terminal_additions.append(_terminal_row(mode, raw))
        secondary_additions.append(_secondary_row(mode, raw))
        seed_additions.append(_seed_audit(mode, raw))
        model_calls = sum(
            int(event["cost"].get("model_calls", 0))
            for row in raw for event in row["events"]
        )
        audit["modes"][mode] = {
            "episodes": len(raw), "model_calls": model_calls,
            "seed_pairs": seed_additions[-1]["seed_pairs"],
            "nonidentical_seed_pairs": seed_additions[-1]["nonidentical_pairs"],
            "SR": float(np.mean([row["metrics"]["SR"] for row in raw])),
            "HTR_commit": float(np.mean([
                row["metrics"]["HTR_commit"] for row in raw
            ])),
            "HTR_all": float(np.mean([row["metrics"]["HTR_all"] for row in raw])),
        }
    _append_rows("paired_condition_differences", paired_additions)
    _append_rows("terminal_distribution", terminal_additions)
    _append_rows("secondary_metrics", secondary_additions)
    _append_seed_audits(seed_additions)
    (EXPORTS / "post_run_probe_then_rag_audit.json").write_text(
        json.dumps(audit, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
