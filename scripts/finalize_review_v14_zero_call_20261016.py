#!/usr/bin/env python3
"""Validate and summarize the preregistered W7b zero-call artifacts."""
from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from pathlib import Path
from statistics import mean

import numpy as np


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/paperA/review_v14_zero_call"
SEED = 20261016


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, data: list[dict[str, object]]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(data[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(data)


def cp_summary(data: list[dict[str, str]]) -> list[dict[str, object]]:
    specifications = (
        ("main", lambda row: row["scope"].startswith("main:"), "task_id"),
        ("main:llm_only", lambda row: row["scope"] == "main:llm_only", "task_id"),
        ("main:all_public", lambda row: row["scope"] == "main:all_public", "task_id"),
        ("main:oracle_reading", lambda row: row["scope"] == "main:oracle_reading", "task_id"),
        ("ext_v2", lambda row: row["scope"] == "ext_v2", "family_id"),
        ("cds", lambda row: row["scope"] == "cds", "family_id"),
    )
    output: list[dict[str, object]] = []
    for index, (label, predicate, unit_field) in enumerate(specifications):
        selected = [row for row in data if predicate(row)]
        grouped: dict[str, list[float]] = defaultdict(list)
        for row in selected:
            grouped[row[unit_field]].append(float(row["SR_delta_vs_naive_rag"]))
        values = np.array([mean(value) for _, value in sorted(grouped.items())])
        rng = np.random.default_rng(SEED + index)
        draws = rng.choice(values, size=(10000, len(values)), replace=True).mean(axis=1)
        cp_sr = mean(float(row["SR"]) for row in selected)
        delta = float(values.mean())
        output.append({
            "scope": label,
            "episodes": len(selected),
            "resampling_units": len(values),
            "cp_rag_SR": cp_sr,
            "naive_SR": cp_sr - delta,
            "SR_delta_vs_naive": delta,
            "ci95_low": float(np.quantile(draws, 0.025)),
            "ci95_high": float(np.quantile(draws, 0.975)),
            "action_equal_naive": mean(float(row["action_equal_naive_rag"]) for row in selected),
            "action_equal_verify_all": mean(float(row["action_equal_verify_all"]) for row in selected),
        })
    return output


def main() -> None:
    cp = rows(OUT / "cp_rag/episode_metrics.csv")
    counts = defaultdict(int)
    for row in cp:
        counts[row["scope"].split(":")[0]] += 1
    assert dict(counts) == {"main": 1122, "ext_v2": 60, "cds": 260}, counts
    cp_effects = cp_summary(cp)
    write_csv(OUT / "cp_rag/paired_effects_detailed.csv", cp_effects)
    cp_lines = [
        "# CP-RAG diagnostic", "",
        "Zero LLM calls. The frozen dev8 policy hash was unchanged; the complete CDS material key",
        "was used only by the evaluation adapter.", "",
        "| Scope | Episodes | Action agreement with Naive | SR difference (95% CI) |",
        "|---|---:|---:|---:|",
    ]
    for row in cp_effects:
        cp_lines.append(
            f"| {row['scope']} | {row['episodes']} | {row['action_equal_naive']:.4f} | "
            f"{row['SR_delta_vs_naive']:.4f} [{row['ci95_low']:.4f}, {row['ci95_high']:.4f}] |"
        )
    (OUT / "cp_rag/REPORT.md").write_text("\n".join(cp_lines) + "\n")

    placebo = rows(OUT / "split_placebo/totals.csv")
    assert {row["placebo"]: int(row["episodes"]) for row in placebo} == {
        "metadata": 3740, "candidate_order": 3740,
    }
    cost = rows(OUT / "search_cost_episode_rescore.csv")
    # The detail table contains one row for each of the three registered prices.
    assert sum(row["scope"] == "pipeline" for row in cost) == 11220 * 3
    assert sum(row["scope"] == "external_static" for row in cost) == 550 * 3
    cost_totals = []
    for price in ("0.1", "0.25", "0.5"):
        for scope in ("pipeline", "external_static"):
            selected = [row for row in cost if row["search_cost"] == price and row["scope"] == scope]
            cost_totals.append({
                "scope": scope, "search_cost": price, "episodes": len(selected),
                "over_budget": sum(int(row["over_budget"]) for row in selected),
                "original_SR": mean(int(row["original_SR"]) for row in selected),
                "rescored_SR": mean(int(row["rescored_SR"]) for row in selected),
            })
    write_csv(OUT / "search_cost_totals.csv", cost_totals)

    cm = rows(OUT / "c_m_by_type.csv")
    assert sum(int(row["n"]) for row in cm) == 673
    reconstruction = rows(OUT / "reconstruction_20_revid.csv")
    assert len(reconstruction) == 20
    assert all(row["snapshot_text_exact"] == "1" and row["wiki_http_requests"] == "0"
               for row in reconstruction)

    lines = [
        "# W7b zero-call diagnostics", "",
        "> All accepted outputs are deterministic and used zero LLM calls. No frozen result was changed.", "",
        "## Validity by coverage", "",
        "The exact O replay gate failed before V/C/V+C outputs were accepted. Therefore dV, dC, d(V+C),",
        "the interaction, and an allocation of the frozen 11.7647 pp gap are unavailable under the",
        "preregistered rule. See `DECOMPOSITION_BLOCKED.md` for the first two exact mismatches.", "",
        "## CP-RAG", "",
        "| Scope | Episodes | CP SR | Naive SR | Difference (95% CI) | Action agreement with Naive |",
        "|---|---:|---:|---:|---:|---:|",
    ]
    for row in cp_effects:
        lines.append(
            f"| {row['scope']} | {row['episodes']} | {row['cp_rag_SR']:.4f} | {row['naive_SR']:.4f} | "
            f"{row['SR_delta_vs_naive']:.4f} [{row['ci95_low']:.4f}, {row['ci95_high']:.4f}] | "
            f"{row['action_equal_naive']:.4f} |"
        )
    lines += ["", "## Split placebo", ""]
    for row in placebo:
        lines.append(
            f"- {row['placebo']}: retrieval sequence {row['retrieval_sequence_changed']}, "
            f"top-k content {row['topk_content_changed']}, terminal {row['terminal_changed']}, "
            f"plan acceptance {row['plan_acceptance_changed']} changes over {row['episodes']} episodes."
        )
    lines += ["", "## Search-cost sensitivity", "",
              "External rows are static rescoring: their policies did not adapt to search price.", "",
              "| Scope | Search cost | Episodes | Over budget | Original SR | Rescored SR |",
              "|---|---:|---:|---:|---:|---:|"]
    for row in cost_totals:
        lines.append(
            f"| {row['scope']} | {row['search_cost']} | {row['episodes']} | {row['over_budget']} | "
            f"{row['original_SR']:.4f} | {row['rescored_SR']:.4f} |"
        )
    lines += ["", "## Adjudication cost", "",
              "| Type | n | Median | Q1-Q3 | Range |", "|---|---:|---:|---:|---:|"]
    for row in cm:
        lines.append(
            f"| {row['type']} | {row['n']} | {float(row['median']):g} | "
            f"{float(row['q1']):g}-{float(row['q3']):g} | "
            f"{float(row['min']):g}-{float(row['max']):g} |"
        )
    lines += ["", "## Reconstruction", "",
              "All 20 preregistered T1/T2 revision samples were recovered exactly from the local public",
              "Internet Archive dump. The check made zero programmatic Wiki requests.", ""]
    (OUT / "REPORT.md").write_text("\n".join(lines))

    manifest_rows = []
    for path in sorted(OUT.rglob("*")):
        if path.is_file() and path.name != "MANIFEST.sha256":
            manifest_rows.append(
                f"{hashlib.sha256(path.read_bytes()).hexdigest()}  {path.relative_to(ROOT)}"
            )
    (OUT / "MANIFEST.sha256").write_text("\n".join(manifest_rows) + "\n")


if __name__ == "__main__":
    main()
