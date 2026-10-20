#!/usr/bin/env python3
"""Registered W16 zero-call analysis from frozen exports only."""

from __future__ import annotations

import csv
import hashlib
import json
from collections import Counter, defaultdict
from pathlib import Path

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/main/post_review/w16_20261019"
SEED = 20261019
N_BOOT = 10_000


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(encoding="utf-8-sig", newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, rows: list[dict]) -> None:
    path = OUT / name
    keys = list(dict.fromkeys(key for row in rows for key in row))
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=keys)
        writer.writeheader()
        writer.writerows(rows)


def number(value: object) -> float:
    if value in (None, ""):
        return float("nan")
    return float(value)


def indicator(value: object) -> float:
    return 1.0 if number(value) != 0 else 0.0


def ci(values: list[float], families: list[str], eligible: list[bool] | None = None) -> tuple[float, float, int, int]:
    """Cluster percentile CI, retaining all selected rows of each family."""
    if eligible is None:
        eligible = [True] * len(values)
    groups: dict[str, list[tuple[float, bool]]] = defaultdict(list)
    for value, family, ok in zip(values, families, eligible):
        groups[family].append((value, ok))
    family_names = sorted(groups)
    numerator = sum(value for value, ok in zip(values, eligible) if ok)
    denominator = sum(eligible)
    point = numerator / denominator if denominator else float("nan")
    rng = np.random.Generator(np.random.PCG64(SEED))
    draws: list[float] = []
    for _ in range(N_BOOT):
        picked = rng.choice(family_names, size=len(family_names), replace=True)
        sampled = [entry for family in picked for entry in groups[family] if entry[1]]
        if sampled:
            draws.append(sum(value for value, _ in sampled) / len(sampled))
    if not draws:
        return point, float("nan"), float("nan"), 0
    return point, float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975)), len(draws)


def paired_ci(rows: list[tuple[str, float]]) -> tuple[float, float, float, int]:
    groups: dict[str, list[float]] = defaultdict(list)
    for family, value in rows:
        groups[family].append(value)
    point = sum(value for _, value in rows) / len(rows) if rows else float("nan")
    names = sorted(groups)
    if not names:
        return point, float("nan"), float("nan"), 0
    rng = np.random.Generator(np.random.PCG64(SEED))
    draws = []
    for _ in range(N_BOOT):
        selected = rng.choice(names, size=len(names), replace=True)
        values = [value for family in selected for value in groups[family]]
        draws.append(sum(values) / len(values))
    return point, float(np.quantile(draws, 0.025)), float(np.quantile(draws, 0.975)), len(draws)


def cds_factorial() -> list[dict]:
    source = ROOT / "reports/main/post_review/cds_agents_20261017/episode_metrics.csv"
    rows = [row for row in read_csv(source) if row["method"] == "react"]
    wanted = ["K+S", "K", "S", "empty", "K+N"]
    grouped = {condition: [row for row in rows if row["condition"] == condition] for condition in wanted}
    output: list[dict] = []
    metrics = [
        ("SR", "success", False),
        ("feasible_SR", "feasible_success", True),
        ("OC", "overconservative", True),
        ("stale_specific_contact", "discriminative_stale_contact", False),
        ("propagation", "v3d_lower", False),
    ]
    for condition, condition_rows in grouped.items():
        for label, field, feasible_only in metrics:
            values = [indicator(row[field]) for row in condition_rows]
            families = [row["family_id"] for row in condition_rows]
            eligible = [indicator(row["true_feasible"]) == 1.0 if feasible_only else True for row in condition_rows]
            point, low, high, draws = ci(values, families, eligible)
            output.append({
                "section": "condition_metric", "scope": "react_glm52_A8", "condition": condition,
                "contrast": "", "metric": label, "estimate": point, "ci95_low": low, "ci95_high": high,
                "n_rows": len(condition_rows), "n_eligible": sum(eligible),
                "n_families": len(set(families)), "bootstrap_draws": draws, "seed": SEED,
                "status": "reported" if len(condition_rows) == 52 else "source_count_mismatch",
            })

    def aligned_difference(left: str, right: str, field: str) -> list[tuple[str, float]]:
        left_map = {row["instance_id"]: row for row in grouped[left]}
        right_map = {row["instance_id"]: row for row in grouped[right]}
        common = sorted(set(left_map) & set(right_map))
        return [(left_map[key]["family_id"], indicator(left_map[key][field]) - indicator(right_map[key][field])) for key in common]

    for left, right in [("S", "empty"), ("S", "K"), ("K+S", "S")]:
        for label, field in [("SR", "success"), ("stale_specific_contact", "discriminative_stale_contact")]:
            point, low, high, draws = paired_ci(aligned_difference(left, right, field))
            output.append({"section": "paired_contrast", "scope": "react_glm52_A8", "condition": "",
                           "contrast": f"{left} - {right}", "metric": label, "estimate": point,
                           "ci95_low": low, "ci95_high": high, "n_rows": len(aligned_difference(left, right, field)),
                           "n_eligible": len(aligned_difference(left, right, field)), "n_families": 12,
                           "bootstrap_draws": draws, "seed": SEED, "status": "reported"})

    for label, field in [("SR", "success"), ("stale_specific_contact", "discriminative_stale_contact")]:
        maps = {condition: {row["instance_id"]: row for row in grouped[condition]} for condition in ["K+S", "K", "S", "empty"]}
        common = sorted(set.intersection(*(set(mapping) for mapping in maps.values())))
        effects: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for key in common:
            rs = {condition: maps[condition][key] for condition in maps}
            family = rs["K+S"]["family_id"]
            values = {condition: indicator(row[field]) for condition, row in rs.items()}
            effects["correct_knowledge_main_effect"].append((family, ((values["K+S"] + values["K"]) / 2) - ((values["S"] + values["empty"]) / 2)))
            effects["stale_knowledge_main_effect"].append((family, ((values["K+S"] + values["S"]) / 2) - ((values["K"] + values["empty"]) / 2)))
            effects["interaction"].append((family, (values["K+S"] - values["K"]) - (values["S"] - values["empty"])))
        for effect, pairs in effects.items():
            point, low, high, draws = paired_ci(pairs)
            output.append({"section": "factorial_2x2", "scope": "react_glm52_A8", "condition": "",
                           "contrast": effect, "metric": label, "estimate": point, "ci95_low": low,
                           "ci95_high": high, "n_rows": len(pairs), "n_eligible": len(pairs),
                           "n_families": 12, "bootstrap_draws": draws, "seed": SEED, "status": "reported"})
    return output


def audit_sensitivity() -> list[dict]:
    source = ROOT / "reports/main/post_review/a19_human_audit_20261017/episode_alignment_80.csv"
    rows = read_csv(source)
    by_key = {(row["method"], row["condition"], row["world"], row["task_id"]): row for row in rows}
    naive = [row for row in rows if row["method"] == "naive_rag"]
    output: list[dict] = []
    methods = sorted({row["method"] for row in rows if row["method"] != "naive_rag"})

    def human_value(row: dict, scheme: str) -> float | None:
        value = row["human_contact"]
        if scheme == "exclude_undetermined" and value == "无法确定":
            return None
        return 1.0 if value in {"查询", "执行"} else 0.0

    def scorer_value(row: dict) -> float:
        return 1.0 if row["scorer_contact"] not in {"", "无", "否", "0"} else 0.0

    for method in methods:
        pairs = []
        for row in rows:
            if row["method"] != method:
                continue
            ref = by_key.get(("naive_rag", row["condition"], row["world"], row["task_id"]))
            if ref:
                pairs.append((row, ref))
        for scheme in ["exclude_undetermined", "confirmed_positive_only"]:
            usable = []
            for row, ref in pairs:
                left, right = human_value(row, scheme), human_value(ref, scheme)
                if left is not None and right is not None:
                    usable.append((left, right, scorer_value(row), scorer_value(ref)))
            status = "audit_covered_paired_subset" if usable else "not_reported_no_audited_naive_pair"
            corrected = sum(left - right for left, right, _, _ in usable) / len(usable) if usable else ""
            frozen = sum(left - right for _, _, left, right in usable) / len(usable) if usable else ""
            output.append({
                "scope": "audit_80_not_full_heldout_110", "method": method, "reference": "naive_rag", "scheme": scheme,
                "n_human_rows_method": sum(row["method"] == method for row in rows), "n_candidate_pairs": len(pairs),
                "n_paired_eligible": len(usable), "full_110_available": "no", "frozen_delta_spec": frozen,
                "human_corrected_delta_spec": corrected,
                "corrected_minus_frozen": (corrected - frozen) if usable else "", "status": status,
            })
    output.append({"scope": "existing_CDS_reference_only", "method": "react_glm52", "reference": "K+N",
                   "scheme": "preexisting_audit_corrected_K+S_minus_K+N", "n_human_rows_method": "",
                   "n_candidate_pairs": 52, "n_paired_eligible": 52, "full_110_available": "not_applicable",
                   "frozen_delta_spec": 0.1923076923, "human_corrected_delta_spec": 0.1730769231,
                   "corrected_minus_frozen": -0.0192307692, "status": "quoted_not_recomputed"})
    return output


def component_bootstrap() -> list[dict]:
    clusters = read_csv(ROOT / "reports/main/acceptance_exports/mechanism_clusters.csv")
    task_to_cluster = {task: row["cluster_id"] for row in clusters for task in row["task_ids"].split(";")}
    source = ROOT / "artifacts/main/all_public/episodes.jsonl"
    records = [json.loads(line) for line in source.open() if '"method": "naive_rag"' in line]
    wanted = {("matched_1.21.11", "1.21.11"), ("mismatched_old2new", "1.21.11"),
              ("matched_1.16.5", "1.16.5"), ("mismatched_new2old", "1.16.5")}
    records = [r for r in records if (r["condition"], r["world"]) in wanted]
    grouped: dict[tuple[str, str, str], list[dict]] = defaultdict(list)
    for record in records:
        grouped[(record["condition"], record["world"], record["task_id"])].append(record)
    duplicates = []
    reps = {}
    for key, values in grouped.items():
        signatures = {(value["metrics"]["SR"], value["reported_feasible"], value["true_feasible"]) for value in values}
        duplicates.append((key, len(values), len(signatures)))
        reps[key] = sorted(values, key=lambda row: row["seed"])[0]
    output: list[dict] = []
    contrasts = [
        ("matched_1.21.11 - mismatched_old2new", "matched_1.21.11", "mismatched_old2new", "1.21.11"),
        ("matched_1.16.5 - mismatched_new2old", "matched_1.16.5", "mismatched_new2old", "1.16.5"),
    ]
    for label, left, right, world in contrasts:
        rows = []
        missing = []
        for task, cluster in task_to_cluster.items():
            a, b = reps.get((left, world, task)), reps.get((right, world, task))
            if not a or not b:
                missing.append(task)
                continue
            rows.append((cluster, indicator(a["metrics"]["SR"]) - indicator(b["metrics"]["SR"])))
        point, low, high, draws = paired_ci(rows)
        output.append({"contrast": label, "metric": "SR", "method": "naive_rag", "reading_mode": "all_public",
                       "estimate": point, "ci95_low": low, "ci95_high": high, "n_tasks": len(rows),
                       "n_components": len(set(cluster for cluster, _ in rows)), "bootstrap_draws": draws,
                       "seed": SEED, "duplicate_seed_cells": len(duplicates),
                       "duplicate_seed_outcome_mismatches": sum(signature_count > 1 for _, _, signature_count in duplicates),
                       "missing_tasks": ";".join(missing), "status": "reported" if not missing else "incomplete_pairs"})
    return output


def cost_table() -> list[dict]:
    output: list[dict] = []
    metrics = read_csv(ROOT / "reports/main/acceptance_exports/secondary_metrics.csv")
    for row in metrics:
        output.append({"method_family": f"pipeline:{row['method']}", "scope": row["reading_mode"],
                       "episodes": row["episodes"], "llm_calls": 0, "estimated_cost_cny": 0,
                       "mean_semantic_actions": row["mean_semantic_actions"], "mean_retrieval_calls": row["mean_searches"],
                       "recorded_replay_duration_seconds": "not_reported_per_method", "source": "secondary_metrics.csv",
                       "status": "reported_existing_record"})
    for mode in ["all_public", "llm_only"]:
        resource = json.loads((ROOT / f"reports/main/resource_{mode}.json").read_text())
        output.append({"method_family": "pipeline_execution", "scope": mode, "episodes": 6732,
                       "llm_calls": 0, "estimated_cost_cny": 0, "mean_semantic_actions": "not_reported_aggregate_execution",
                       "mean_retrieval_calls": "not_reported_aggregate_execution",
                       "recorded_replay_duration_seconds": resource.get("elapsed_seconds"),
                       "source": f"resource_{mode}.json", "status": f"recorded_wrapper_exit_{resource.get('exit_code')}"})
    ext_rows = read_csv(ROOT / "reports/main/post_review/a17_ext_agents_20261017/per_episode.csv")
    ext_cost = json.loads((ROOT / "reports/main/post_review/a17_ext_agents_20261017/actual_usage_and_estimated_cost.json").read_text())
    output.append({"method_family": "external_glm52_main", "scope": "A17_110_submatrix", "episodes": len(ext_rows),
                   "llm_calls": ext_cost.get("task_responses"), "estimated_cost_cny": ext_cost.get("token_rate_estimated_CNY"),
                   "mean_semantic_actions": sum(number(r["semantic_actions"]) for r in ext_rows) / len(ext_rows),
                   "mean_retrieval_calls": sum(number(r["search_calls"]) for r in ext_rows) / len(ext_rows),
                   "recorded_replay_duration_seconds": "not_reported", "source": "A17 per_episode + usage ledger",
                   "status": "reported_existing_record"})
    cds_rows = read_csv(ROOT / "reports/main/post_review/cds_agents_20261017/episode_metrics.csv")
    for method in sorted({row["method"] for row in cds_rows}):
        rows = [row for row in cds_rows if row["method"] == method]
        cost_values = [number(row["provider_cost_cny"]) for row in rows if row["provider_cost_cny"]]
        output.append({"method_family": f"CDS:{method}", "scope": "A8_CDS", "episodes": len(rows),
                       "llm_calls": sum(number(row["action_model_calls"]) + number(row["boundary_model_calls"]) for row in rows),
                       "estimated_cost_cny": sum(cost_values) if cost_values else "not_reported",
                       "mean_semantic_actions": sum(number(row["semantic_actions"]) for row in rows) / len(rows),
                       "mean_retrieval_calls": 0, "recorded_replay_duration_seconds": "not_reported",
                       "source": "cds_agents_20261017/episode_metrics.csv", "status": "reported_existing_record"})
    for label, relative, completeness in [
        ("CDS:qwen_remote_react", "reports/main/post_review/a18_qwen_cds_20261017/remote_service/episode_metrics.csv", "A18_remote_208"),
        ("CDS:qwen_local_nf4_partial", "reports/main/post_review/a18_qwen_cds_20261017/local_nf4/episode_metrics.csv", "A18_partial_7"),
    ]:
        rows = read_csv(ROOT / relative)
        output.append({"method_family": label, "scope": completeness, "episodes": len(rows),
                       "llm_calls": sum(number(row["action_model_calls"]) + number(row["boundary_model_calls"]) for row in rows),
                       "estimated_cost_cny": "not_reported_no_priced_ledger",
                       "mean_semantic_actions": sum(number(row["semantic_actions"]) for row in rows) / len(rows),
                       "mean_retrieval_calls": 0, "recorded_replay_duration_seconds": "not_reported",
                       "source": relative, "status": "reported_existing_record"})
    return output


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    factorial = cds_factorial()
    audit = audit_sensitivity()
    components = component_bootstrap()
    costs = cost_table()
    write_csv("factorial_s.csv", factorial)
    write_csv("audit_sensitivity.csv", audit)
    write_csv("component_bootstrap.csv", components)
    write_csv("cost_table.csv", costs)
    inputs = [
        ROOT / "reports/main/post_review/cds_agents_20261017/episode_metrics.csv",
        ROOT / "reports/main/post_review/a19_human_audit_20261017/episode_alignment_80.csv",
        ROOT / "artifacts/main/all_public/episodes.jsonl",
        ROOT / "reports/main/acceptance_exports/mechanism_clusters.csv",
        ROOT / "reports/main/acceptance_exports/secondary_metrics.csv",
    ]
    hashes = {str(path.relative_to(ROOT)): hashlib.sha256(path.read_bytes()).hexdigest() for path in inputs}
    (OUT / "README.md").write_text(
        "# W16 zero-call reanalysis package II\n\n"
        "Registered at `prereg/w16_post_review_v_20261019.md`. No LLM calls or new episodes were run.\n\n"
        f"Seed: `{SEED}`; bootstrap draws: `{N_BOOT}`.\n\n"
        "The audit export contains 80 reviewed rows, not a full labeled held-out-110 population; "
        "`audit_sensitivity.csv` therefore reports only exact paired audit coverage and never extrapolates.\n\n"
        "## Input SHA-256\n\n" + "\n".join(f"- `{path}`: `{digest}`" for path, digest in hashes.items()) + "\n",
        encoding="utf-8",
    )


if __name__ == "__main__":
    main()
