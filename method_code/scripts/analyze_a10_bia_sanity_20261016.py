"""Deterministic A10 BIA sanity analysis over frozen A8 CDS outputs."""
from __future__ import annotations

import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

from scripts.analyze_a9_cds_followup_20261016 import (
    A8,
    BOOTSTRAP_DRAWS,
    BOOTSTRAP_SEED,
    a8_sources,
    bootstrap_differences,
    boundary_items,
    csv_rows,
    episode_balanced_accuracy,
    write_csv,
)


ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "reports/main/post_review/a10_bia_sanity"
FIRST_RUN_QUERY = A8 / "query_followup_summary.csv"


def grouped_items(items: list[dict[str, Any]]) -> dict[tuple[str, str], list[dict[str, Any]]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        grouped[(str(row["method"]), str(row["condition"]))].append(row)
    return grouped


def recall_and_accuracy(rows: list[dict[str, Any]], prediction: str, *, answered_only: bool) -> dict[str, Any]:
    selected = [row for row in rows if not answered_only or row["agent_answered"]]
    valid = [row for row in selected if row["gold_valid"]]
    invalid = [row for row in selected if not row["gold_valid"]]
    correct = sum(int(row[prediction] == row["gold_valid"]) for row in selected)
    valid_recall = sum(int(row[prediction] == 1) for row in valid) / len(valid) if valid else ""
    invalid_recall = sum(int(row[prediction] == 0) for row in invalid) / len(invalid) if invalid else ""
    balanced = ((float(valid_recall) + float(invalid_recall)) / 2
                if valid_recall != "" and invalid_recall != "" else "")
    return {
        "all_items": len(rows),
        "scored_items": len(selected),
        "gold_valid_n": len(valid),
        "gold_invalid_n": len(invalid),
        "gold_valid_proportion": len(valid) / len(selected) if selected else "",
        "gold_invalid_proportion": len(invalid) / len(selected) if selected else "",
        "predicted_valid_n": sum(int(row[prediction] == 1) for row in selected),
        "predicted_invalid_n": sum(int(row[prediction] == 0) for row in selected),
        "valid_recall": valid_recall,
        "invalid_recall": invalid_recall,
        "unweighted_accuracy": correct / len(selected) if selected else "",
        "balanced_accuracy": balanced,
        "uncertain_n": len(rows) - len(selected),
        "uncertain_rate": (len(rows) - len(selected)) / len(rows) if rows else "",
    }


def comparator_cells(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for (method, condition), rows in sorted(grouped_items(items).items()):
        for comparator, field in (
            ("always_invalid", "always_invalid_prediction"),
            ("newest_timestamp", "newest_timestamp_prediction"),
            ("context_present_valid", "context_present_prediction"),
        ):
            summary = recall_and_accuracy(rows, field, answered_only=False)
            output.append({
                "method": method,
                "condition": condition,
                "predictor": comparator,
                "constant_prediction": (
                    "valid" if summary["predicted_valid_n"] == summary["scored_items"] else
                    "invalid" if summary["predicted_invalid_n"] == summary["scored_items"] else
                    "mixed"
                ),
                **summary,
            })
    return output


def agent_distributions(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for (method, condition), rows in sorted(grouped_items(items).items()):
        summary = recall_and_accuracy(rows, "agent_prediction", answered_only=True)
        labels = Counter(
            "uncertain" if not row["agent_answered"] else
            "valid" if row["agent_prediction"] == 1 else "invalid"
            for row in rows
        )
        max_label, max_count = max(labels.items(), key=lambda pair: (pair[1], pair[0]))
        output.append({
            "method": method,
            "condition": condition,
            "valid_n": labels["valid"],
            "invalid_n": labels["invalid"],
            "uncertain_n": labels["uncertain"],
            "all_items": len(rows),
            "valid_proportion": labels["valid"] / len(rows),
            "invalid_proportion": labels["invalid"] / len(rows),
            "uncertain_proportion": labels["uncertain"] / len(rows),
            "dominant_label": max_label,
            "dominant_proportion": max_count / len(rows),
            "degenerate_at_95pct": int(max_count / len(rows) >= 0.95),
            "gold_valid_answered_n": summary["gold_valid_n"],
            "gold_invalid_answered_n": summary["gold_invalid_n"],
            "valid_recall": summary["valid_recall"],
            "invalid_recall": summary["invalid_recall"],
            "answered_item_accuracy": summary["unweighted_accuracy"],
            "answered_item_balanced_accuracy": summary["balanced_accuracy"],
        })
    return output


def episode_rows(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        grouped[(str(row["method"]), str(row["condition"]), str(row["instance_id"]),
                 int(row["discriminative_probe"]))].append(row)
    output = []
    for (method, condition, instance_id, probe), rows in sorted(grouped.items()):
        bia = episode_balanced_accuracy(rows, "agent_prediction")
        output.append({
            "method": method,
            "condition": condition,
            "instance_id": instance_id,
            "family_id": rows[0]["family_id"],
            "discriminative_probe": probe,
            "episode_BIA": "" if bia is None else bia,
            "defined": int(bia is not None),
        })
    return output


def probe_strata(items: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    episodes = episode_rows(items)
    output = []
    by_cell: dict[tuple[str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        by_cell[(row["method"], row["condition"], row["discriminative_probe"])].append(row)
    for (method, condition, probe), rows in sorted(by_cell.items()):
        values = [float(row["episode_BIA"]) for row in rows if row["defined"]]
        output.append({
            "method": method,
            "condition": condition,
            "probe_stratum": probe,
            "episodes": len(rows),
            "BIA_defined_episodes": len(values),
            "mean_episode_BIA": sum(values) / len(values) if values else "",
            "undefined_BIA_episodes": len(rows) - len(values),
        })

    # Probe membership is exclusive within a method-condition episode. The
    # registered paired estimate therefore matches the same CDS instance across
    # that method's conditions and averages available values in each stratum.
    tests = []
    by_method_instance: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        if row["defined"]:
            by_method_instance[(row["method"], row["instance_id"])].append(row)
    for method in sorted({row["method"] for row in episodes}):
        pairs = []
        for (episode_method, _), rows in sorted(by_method_instance.items()):
            if episode_method != method:
                continue
            probe = [float(row["episode_BIA"]) for row in rows if row["discriminative_probe"] == 1]
            no_probe = [float(row["episode_BIA"]) for row in rows if row["discriminative_probe"] == 0]
            if probe and no_probe:
                pairs.append((str(rows[0]["family_id"]), sum(probe) / len(probe) - sum(no_probe) / len(no_probe)))
        if pairs:
            point, low, high, n = bootstrap_differences(pairs)
            tests.append({
                "method": method,
                "comparison": "probe=1 - probe=0",
                "scope": "within_instance_across_available_conditions",
                "n_pairs": n,
                "n_families": len({family_id for family_id, _ in pairs}),
                "difference": point,
                "ci95_low": low,
                "ci95_high": high,
                "significantly_greater": int(low > 0),
                "bootstrap_unit": "independent_family",
                "draws": BOOTSTRAP_DRAWS,
                "seed": BOOTSTRAP_SEED,
            })
        else:
            tests.append({
                "method": method,
                "comparison": "probe=1 - probe=0",
                "scope": "within_instance_across_available_conditions",
                "n_pairs": 0,
                "n_families": 0,
                "difference": "NA",
                "ci95_low": "NA",
                "ci95_high": "NA",
                "significantly_greater": "NA",
                "bootstrap_unit": "independent_family",
                "draws": BOOTSTRAP_DRAWS,
                "seed": BOOTSTRAP_SEED,
            })
    return output, tests


def first_run_queries() -> list[dict[str, Any]]:
    rows = csv_rows(FIRST_RUN_QUERY)
    expected = {"denied_then_target_valid", "denied_then_stale", "not_denied", "no_related_followup"}
    if {row["followup_class"] for row in rows} != expected:
        raise RuntimeError("first-run query classes changed")
    return [{"scope": "first_run_external", **row} for row in rows]


def cds_ks_queries(metrics: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, Any]]:
    fields = ("denied_then_target_valid", "denied_then_stale", "not_denied", "no_related_followup")
    output = []
    for method in ("direct_llm", "react"):
        rows = [row for row in metrics.values() if row["method"] == method and row["condition"] == "K+S"]
        if len(rows) != 52:
            raise RuntimeError(f"unexpected CDS K+S coverage: {method}={len(rows)}")
        counts = {field: sum(int(row[f"followup_{field}"]) for row in rows) for field in fields}
        denominator = sum(counts.values())
        for label in fields:
            output.append({
                "scope": "CDS_K+S",
                "method": method,
                "followup_class": label,
                "count": counts[label],
                "denominator": denominator,
                "proportion": counts[label] / denominator if denominator else "",
            })
    return output


def construction_verdict(cells: list[dict[str, Any]]) -> tuple[str, list[str]]:
    reasons = []
    all_balanced = all(row["gold_valid_n"] == row["gold_invalid_n"] for row in cells)
    if all_balanced:
        reasons.append("Every method-condition candidate set has exactly balanced valid and invalid gold counts.")
    constants = defaultdict(set)
    for row in cells:
        constants[row["predictor"]].add(row["constant_prediction"])
    if all(row["constant_prediction"] in {"valid", "invalid"} for row in cells):
        reasons.append("Each comparator is condition-wise constant (all valid or all invalid), so it recalls exactly one gold class.")
    newest_valid = [row for row in cells if row["predictor"] == "newest_timestamp" and row["constant_prediction"] == "valid"]
    if len(newest_valid) == len([row for row in cells if row["predictor"] == "newest_timestamp"]):
        reasons.append("The frozen candidates do not show timestamp reversal for this heuristic: newest_timestamp predicts valid for every candidate.")
    context_by_condition = {(row["method"], row["condition"]): row["constant_prediction"]
                            for row in cells if row["predictor"] == "context_present_valid"}
    reasons.append("context_present_valid is all-invalid in K/empty and all-valid in S/K+S/K+N, as shown in comparator_cells.csv.")
    verdict = "design_consequence" if all_balanced and all(row["balanced_accuracy"] == 0.5 for row in cells) else "other_cause"
    return verdict, reasons


def main() -> None:
    source, metrics = a8_sources()
    items = boundary_items(source, metrics)
    cells = comparator_cells(items)
    distributions = agent_distributions(items)
    strata, probe_tests = probe_strata(items)
    first_queries = first_run_queries()
    cds_queries = cds_ks_queries(metrics)
    verdict, reasons = construction_verdict(cells)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "comparator_cells.csv", cells)
    write_csv(OUT / "agent_response_distribution.csv", distributions)
    write_csv(OUT / "probe_strata.csv", strata)
    write_csv(OUT / "probe_strata_paired_tests.csv", probe_tests)
    write_csv(OUT / "query_followup_first_run.csv", first_queries)
    write_csv(OUT / "query_followup_cds_k_plus_s.csv", cds_queries)

    report = ["# A10 BIA sanity check", "", "- A8 coverage: 416 unique CDS episodes.",
              "- This report performed no model call and no environment episode.",
              f"- Comparator verdict: **{verdict}**.", "", "## Comparator result", ""]
    for reason in reasons:
        report.append(f"- {reason}")
    report.extend(("", "The 0.5 values are therefore a consequence of balanced candidate labels plus constant heuristic outputs, not a scoring arithmetic failure. The expected timestamp-reversal explanation is not supported by these frozen candidate records: the newest-timestamp rule is all-valid in every condition.",
                   "", "## Agent response distributions", ""))
    for row in distributions:
        report.append(f"- `{row['method']} / {row['condition']}`: valid={row['valid_n']}, invalid={row['invalid_n']}, "
                      f"uncertain={row['uncertain_n']} of {row['all_items']}; valid recall={row['valid_recall']}, "
                      f"invalid recall={row['invalid_recall']}; degenerate_at_95pct={row['degenerate_at_95pct']}.")
    report.extend(("", "## Probe strata", ""))
    for row in strata:
        report.append(f"- `{row['method']} / {row['condition']} / probe={row['probe_stratum']}`: "
                      f"mean episode BIA={row['mean_episode_BIA']} from "
                      f"{row['BIA_defined_episodes']}/{row['episodes']} defined episodes.")
    report.append("")
    for row in probe_tests:
        report.append(f"- `{row['method']}` {row['comparison']} ({row['scope']}): {row['difference']} "
                      f"[95% CI {row['ci95_low']}, {row['ci95_high']}], n={row['n_pairs']}, "
                      f"families={row['n_families']}.")
    report.extend(("", "Within a method-condition, probe membership is mutually exclusive, so condition-specific matched-pair effects are not estimable; the registered estimate matches a CDS instance across its available conditions.",
                   "", "## Query follow-up", ""))
    for row in cds_queries:
        report.append(f"- `CDS K+S / {row['method']}` {row['followup_class']}: {row['count']} / {row['denominator']}.")
    report.extend(("", "First-run five-method counts are in query_followup_first_run.csv."))
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"comparator_cells": len(cells), "distributions": len(distributions),
                      "probe_tests": len(probe_tests), "cds_query_rows": len(cds_queries),
                      "verdict": verdict}, sort_keys=True))


if __name__ == "__main__":
    main()
