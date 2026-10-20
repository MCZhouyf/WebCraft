"""Deterministic A9 follow-up analysis over frozen A8 CDS logs."""
from __future__ import annotations

import ast
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable

import numpy as np

from arbiter.evaluation.consequence_htr import inventory_dependencies
from scripts.analyze_cds_agents_20261017 import (
    DEFAULT_INPUT, ROOT, WEBCRAFT, inputs_absent, records,
)
from scripts.run_cds_zero_call_20261017 import EXTERNAL, a7_records, jsonl, pricing_fit, token_scope


OUT = ROOT / "reports/main/post_review/a9_cds_followup"
A8 = ROOT / "reports/main/post_review/cds_agents_20261017"
BOOTSTRAP_SEED = 20261017
BOOTSTRAP_DRAWS = 10_000
AUDIT_SEED = 20261016


def write_csv(path: Path, rows: list[dict[str, Any]], fields: list[str] | None = None) -> None:
    if not rows:
        raise RuntimeError(f"refusing empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    if fields is None:
        fields = []
        for row in rows:
            fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def norm(text: str) -> str:
    return " ".join(str(text).lower().split())


def key(row: dict[str, Any]) -> tuple[str, str, str]:
    return str(row["method"]), str(row["condition"]), str(row["instance_id"])


def a8_sources() -> tuple[dict[tuple[str, str, str], dict[str, Any]], dict[tuple[str, str, str], dict[str, str]]]:
    source = {key(row): row for row in records(DEFAULT_INPUT, allow_partial=False)}
    metrics = {key(row): row for row in csv_rows(A8 / "episode_metrics.csv")}
    if len(source) != 416 or len(metrics) != 416 or set(source) != set(metrics):
        raise RuntimeError("A8 CDS coverage changed")
    return source, metrics


def visible_facts(record: dict[str, Any]) -> set[str]:
    material = json.loads((WEBCRAFT / record["visible_material_path"]).read_text())
    rows = material["conditions"][record["condition"]]
    facts = {norm(row["mechanism fact"]) for row in rows}
    for search in record.get("search_observations", []):
        for row in search.get("result", []):
            if isinstance(row, dict) and "mechanism fact" in row:
                facts.add(norm(row["mechanism fact"]))
    return facts


def boundary_items(source: dict[tuple[str, str, str], dict[str, Any]],
                   metrics: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    for record_key, record in source.items():
        metric = metrics[record_key]
        material = json.loads((WEBCRAFT / record["visible_material_path"]).read_text())
        candidates = material["boundary_question"]
        truth = record["boundary_truth"]
        prediction = record["boundary_predictions"]
        if not (len(candidates) == len(truth) == len(prediction)):
            raise RuntimeError(f"boundary alignment changed: {record_key}")
        facts = visible_facts(record)
        newest = max(str(row["timestamp"]) for row in candidates)
        for index, (candidate, gold, answer) in enumerate(zip(candidates, truth, prediction), start=1):
            output.append({
                "method": record["method"], "condition": record["condition"],
                "instance_id": record["instance_id"], "family_id": metric["family_id"],
                "discriminative_probe": int(metric["discriminative_probe"]), "candidate_index": index,
                "gold_valid": int(bool(gold)), "agent_prediction": "uncertain" if answer is None else int(bool(answer)),
                "agent_answered": int(answer is not None), "always_invalid_prediction": 0,
                "newest_timestamp_prediction": int(str(candidate["timestamp"]) == newest),
                "context_present_prediction": int(norm(candidate["mechanism fact"]) in facts),
            })
    return output


def binary_summary(items: list[dict[str, Any]], prediction: str) -> dict[str, Any]:
    selected = items if prediction != "agent_prediction" else [row for row in items if row["agent_answered"]]
    valid = [row for row in selected if row["gold_valid"]]
    invalid = [row for row in selected if not row["gold_valid"]]
    correct = sum(int(row[prediction] == row["gold_valid"]) for row in selected)
    valid_recall = (sum(int(row[prediction] == 1) for row in valid) / len(valid)) if valid else ""
    invalid_recall = (sum(int(row[prediction] == 0) for row in invalid) / len(invalid)) if invalid else ""
    ba = ((float(valid_recall) + float(invalid_recall)) / 2
          if valid_recall != "" and invalid_recall != "" else "")
    all_count = len(items)
    return {
        "items": all_count, "answered_items": len(selected), "gold_valid_n": len(valid),
        "gold_invalid_n": len(invalid), "gold_valid_proportion": len(valid) / len(selected) if selected else "",
        "gold_invalid_proportion": len(invalid) / len(selected) if selected else "",
        "BIA": correct / len(selected) if selected else "", "valid_recall": valid_recall,
        "invalid_recall": invalid_recall, "balanced_accuracy": ba,
        "uncertain_rate": ((all_count - len(selected)) / all_count) if all_count else "",
        "majority_class_baseline": max(len(valid), len(invalid)) / len(selected) if selected else "",
    }


def bia_summary(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        grouped[(row["method"], row["condition"], "all")].append(row)
        grouped[(row["method"], row["condition"], str(row["discriminative_probe"]))].append(row)
    output = []
    for (method, condition, stratum), values in sorted(grouped.items()):
        for label, field in (
            ("agent", "agent_prediction"), ("always_invalid", "always_invalid_prediction"),
            ("newest_timestamp", "newest_timestamp_prediction"),
            ("context_present_valid", "context_present_prediction"),
        ):
            output.append({
                "method": method, "condition": condition, "probe_stratum": stratum,
                "predictor": label, **binary_summary(values, field),
            })
    return output


def episode_balanced_accuracy(items: list[dict[str, Any]], prediction: str) -> float | None:
    selected = items if prediction != "agent_prediction" else [row for row in items if row["agent_answered"]]
    valid = [row for row in selected if row["gold_valid"]]
    invalid = [row for row in selected if not row["gold_valid"]]
    if not valid or not invalid:
        return None
    return ((sum(int(row[prediction] == 1) for row in valid) / len(valid))
            + (sum(int(row[prediction] == 0) for row in invalid) / len(invalid))) / 2


def bootstrap_differences(pairs: list[tuple[str, float]], seed: int = BOOTSTRAP_SEED) -> tuple[float, float, float, int]:
    families: dict[str, list[float]] = defaultdict(list)
    for family_id, difference in pairs:
        families[family_id].append(difference)
    values = np.asarray([mean(value) for value in families.values()], dtype=float)
    if not len(values):
        return float("nan"), float("nan"), float("nan"), 0
    rng = np.random.default_rng(seed)
    draws = values[rng.integers(0, len(values), size=(BOOTSTRAP_DRAWS, len(values)))].mean(axis=1)
    low, high = np.quantile(draws, [0.025, 0.975])
    return float(values.mean()), float(low), float(high), sum(len(value) for value in families.values())


def bia_pairs(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in items:
        grouped[(row["method"], row["condition"], row["instance_id"])].append(row)
    output = []
    for method in ("direct_llm", "react"):
        pairs_by_baseline: dict[str, list[tuple[str, float]]] = defaultdict(list)
        for (episode_method, condition, _), rows in grouped.items():
            if episode_method != method or condition != "K+S":
                continue
            agent = episode_balanced_accuracy(rows, "agent_prediction")
            if agent is None:
                continue
            for baseline, field in (
                ("always_invalid", "always_invalid_prediction"),
                ("newest_timestamp", "newest_timestamp_prediction"),
                ("context_present_valid", "context_present_prediction"),
            ):
                value = episode_balanced_accuracy([row for row in rows if row["agent_answered"]], field)
                if value is not None:
                    pairs_by_baseline[baseline].append((rows[0]["family_id"], agent - value))
        for baseline, pairs in pairs_by_baseline.items():
            point, low, high, n = bootstrap_differences(pairs)
            output.append({
                "method": method, "condition": "K+S", "metric": "answered_item_balanced_accuracy",
                "comparison": f"agent - {baseline}", "n_pairs": n, "difference": point,
                "ci95_low": low, "ci95_high": high, "significantly_greater": int(low > 0),
                "bootstrap_unit": "independent_family", "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            })
    return output


def list_value(value: str) -> list[Any]:
    return list(ast.literal_eval(value)) if value else []


def propagation_events(source: dict[tuple[str, str, str], dict[str, Any]],
                       action_rows: list[dict[str, str]], metrics: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    event_cache: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for row in action_rows:
        if row["v3d_propagation"] != "1" or row["condition"] not in {"K+S", "K+N"}:
            continue
        episode_key = (row["episode_method"], row["condition"], row["instance_id"])
        record = source[episode_key]
        events = event_cache.setdefault(episode_key, jsonl(ROOT / record["action_log"]))
        index = int(row["event_index"])
        predicted = set(str(value).removeprefix("minecraft:") for value in list_value(row["predicted_products"]))
        referenced = [int(value) for value in list_value(row["referenced_at"])]
        dependencies = inventory_dependencies(events)
        fifo = any(consumer > index and name in predicted for consumer, name, _ in dependencies.get(index, []))
        absent = any(inputs_absent(events, later, predicted) for later in referenced)
        rules = []
        if fifo or absent:
            rules.append("i:predicted_product_reference" + ("_fifo" if fifo else "_absent"))
        if list_value(row["repeated_at"]):
            rules.append("ii:exact_stale_action_repeat")
        if row["affirmative_submit_at"]:
            rules.append("iii:affirmative_submission")
        if not rules:
            raise RuntimeError(f"unable to recover v3-d trigger: {episode_key} / {index}")
        later = next((event for event in events[index + 1:] if event.get("method") != "search"), None)
        output.append({
            "method": row["episode_method"], "condition": row["condition"],
            "instance_id": row["instance_id"], "family_id": metrics[episode_key]["family_id"],
            "task_id": metrics[episode_key]["task_id"], "event_index": index,
            "trigger_rules": ";".join(rules), "stale_action": json.dumps(events[index], sort_keys=True),
            "world_feedback": json.dumps(events[index].get("result"), sort_keys=True),
            "next_relevant_action": json.dumps(later, sort_keys=True) if later else "",
            "terminal": metrics[episode_key]["terminal"],
        })
    return output


def overlap(metrics: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, Any]]:
    output = []
    for method, condition in (("direct_llm", "K+S"), ("react", "K+S"), ("react", "K+N")):
        rows = [row for row in metrics.values() if row["method"] == method and row["condition"] == condition]
        contact = {row["instance_id"] for row in rows if row["discriminative_stale_contact"] == "1"}
        propagation = {row["instance_id"] for row in rows if row["v3d_lower"] == "1"}
        output.append({
            "method": method, "condition": condition, "n": len(rows),
            "contact_n": len(contact), "propagation_n": len(propagation),
            "intersection_n": len(contact & propagation), "union_n": len(contact | propagation),
            "contact_only_n": len(contact - propagation), "propagation_only_n": len(propagation - contact),
            "contact_ids": ";".join(sorted(contact)), "propagation_ids": ";".join(sorted(propagation)),
            "intersection_ids": ";".join(sorted(contact & propagation)),
        })
    return output


def paired_contrasts(metrics: dict[tuple[str, str, str], dict[str, str]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, str]]] = defaultdict(list)
    for row in metrics.values():
        grouped[(row["method"], row["condition"])].append(row)
    comparisons = (
        ("react", "S", "K"), ("react", "S", "empty"), ("react", "K+S", "K"),
        ("direct_llm", "K+S", "K"), ("direct_llm", "empty", "K"),
        ("direct_llm", "K", "react", "K"),
    )
    measures: tuple[tuple[str, str, Callable[[dict[str, str]], bool]], ...] = (
        ("SR", "success", lambda row: True),
        ("feasible_SR", "success", lambda row: row["true_feasible"] == "1"),
        ("overconservatism", "overconservative", lambda row: row["true_feasible"] == "1"),
        ("terminal_execution_failure", "terminal_execution_failure", lambda row: True),
        ("first_conflict_target_agreement", "first_conflict_target_agreement",
         lambda row: row["first_conflict_target_agreement"] != ""),
        ("discriminative_stale_contact", "discriminative_stale_contact", lambda row: True),
        ("v3d_lower", "v3d_lower", lambda row: True),
    )
    output = []
    for comparison in comparisons:
        if len(comparison) == 3:
            method, left_condition, right_condition = comparison
            label = f"{method} {left_condition} - {right_condition}"
            left, right = grouped[(method, left_condition)], grouped[(method, right_condition)]
        else:
            left_method, left_condition, right_method, right_condition = comparison
            label = f"{left_method} {left_condition} - {right_method} {right_condition}"
            left, right = grouped[(left_method, left_condition)], grouped[(right_method, right_condition)]
        for label_metric, field, eligible in measures:
            left_by = {row["instance_id"]: row for row in left if eligible(row)}
            right_by = {row["instance_id"]: row for row in right if eligible(row)}
            pairs = [(left_by[item]["family_id"], float(left_by[item][field]) - float(right_by[item][field]))
                     for item in sorted(set(left_by) & set(right_by))]
            point, low, high, n = bootstrap_differences(pairs)
            output.append({
                "comparison": label, "metric": label_metric, "n_pairs": n, "difference": point,
                "ci95_low": low, "ci95_high": high, "bootstrap_unit": "independent_family",
                "draws": BOOTSTRAP_DRAWS, "seed": BOOTSTRAP_SEED,
            })
    return output


def query_counts() -> list[dict[str, str]]:
    return csv_rows(A8 / "query_followup_summary.csv")


def cost_scopes() -> dict[str, Any]:
    pricing = pricing_fit()
    first = [row for path in EXTERNAL.values() for row in jsonl(path)]
    same_model = jsonl(ROOT / "artifacts/post_review/external_second_model_probe/episodes.jsonl")
    locked = jsonl(ROOT / "artifacts/post_review/alias_locked_replication/episodes.jsonl")
    scopes = [
        token_scope("external_first_run_five_methods", first, pricing),
        token_scope("same_model_rerun", same_model, pricing),
        token_scope("alias_locked_replication", locked, pricing),
        token_scope("A7_diagnostic_controls", a7_records(), pricing),
    ]
    return {"estimated": True, "currency": pricing["currency"], "pricing": pricing, "scopes": scopes,
            "uncertainty": [
                "Historical episode exports do not retain cached-input totals; the uncached estimate and all-input-cached lower bound bracket the input component.",
                "Provider-side discounts, hidden tokens, and unrecorded billing adjustments are unavailable.",
                "No currency conversion is applied.",
            ]}


def short_event(event: dict[str, Any]) -> str:
    result = event.get("result")
    if isinstance(result, dict):
        result = {key: result.get(key) for key in ("success", "error", "product", "items", "resolvable") if key in result}
    elif isinstance(result, list):
        result = [{"mechanism fact": row.get("mechanism fact"), "source": row.get("source")}
                  if isinstance(row, dict) else row for row in result[:2]]
    return json.dumps({"method": event.get("method"), "params": event.get("params"),
                       "success": event.get("success"), "result": result}, ensure_ascii=False, sort_keys=True)


def audit_sheet(source: dict[tuple[str, str, str], dict[str, Any]],
                metrics: dict[tuple[str, str, str], dict[str, str]],
                action_rows: list[dict[str, str]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    action_types: dict[tuple[str, str, str], set[str]] = defaultdict(set)
    for row in action_rows:
        if row["discriminative"] == "1":
            action_types[(row["episode_method"], row["condition"], row["instance_id"])].add(row["action_type"])
    strata: dict[str, list[tuple[str, str, str]]] = defaultdict(list)
    for record_key, row in metrics.items():
        if row["condition"] != "K+S":
            continue
        strata["propagation" if row["v3d_lower"] == "1" else
                "contact_without_propagation" if row["discriminative_stale_contact"] == "1" else
                "no_contact"].append(record_key)
    rng = np.random.default_rng(AUDIT_SEED)
    selected: list[tuple[str, tuple[str, str, str]]] = []
    for stratum in ("propagation", "contact_without_propagation", "no_contact"):
        candidates = sorted(strata[stratum])
        if len(candidates) < 10:
            raise RuntimeError(f"insufficient audit candidates for {stratum}: {len(candidates)}")
        chosen = sorted(rng.choice(len(candidates), size=10, replace=False).tolist())
        selected.extend((stratum, candidates[index]) for index in chosen)
    visible, secret = [], []
    fields = ["trajectory_id", "agent_family", "method", "condition", "world", "task_id",
              "action_sequence_and_feedback", "search_snippets", "contact_type", "propagation",
              "terminal_depends_on_stale", "notes"]
    for index, (stratum, record_key) in enumerate(selected, start=1):
        record, metric = source[record_key], metrics[record_key]
        events = jsonl(ROOT / record["action_log"])
        shown = events[:20]
        if len(events) > 23:
            shown += events[-3:]
        sequence = " -> ".join(short_event(event) for event in shown)
        if len(events) > 23:
            sequence = sequence.replace(short_event(events[19]) + " -> " + short_event(events[-3]),
                                        short_event(events[19]) + " -> [truncated] -> " + short_event(events[-3]))
        snippets = []
        for event in events:
            if event.get("method") == "search" and isinstance(event.get("result"), list):
                snippets.extend(str(row.get("mechanism fact", ""))[:240] for row in event["result"][:2])
            if len(snippets) >= 2:
                break
        trajectory_id = f"A9-CDS-{index:03d}"
        visible.append({
            "trajectory_id": trajectory_id, "agent_family": "cds", "method": record["method"],
            "condition": record["condition"], "world": record["target_world"], "task_id": record["task_id"],
            "action_sequence_and_feedback": sequence, "search_snippets": "\n---\n".join(snippets),
            "contact_type": "", "propagation": "", "terminal_depends_on_stale": "", "notes": "",
        })
        contact = action_types.get(record_key, set())
        secret.append({
            "trajectory_id": trajectory_id, "stratum": stratum,
            "contact_type": "+".join(sorted(contact)) if contact else "none",
            "propagation": "yes" if metric["v3d_lower"] == "1" else "no",
            "terminal_depends_on_stale": "undetermined", "notes": "saved-log scorer label",
        })
    return visible, secret


def main() -> None:
    source, metrics = a8_sources()
    items = boundary_items(source, metrics)
    actions = csv_rows(A8 / "stale_action_audit.csv")
    bia = bia_summary(items)
    paired_bia = bia_pairs(items)
    propagation = propagation_events(source, actions, metrics)
    overlap_rows = overlap(metrics)
    contrasts = paired_contrasts(metrics)
    audit_visible, audit_key = audit_sheet(source, metrics, actions)
    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "bia_items.csv", items)
    write_csv(OUT / "bia_summary.csv", bia)
    write_csv(OUT / "bia_paired_tests.csv", paired_bia)
    write_csv(OUT / "contact_propagation_overlap.csv", overlap_rows)
    write_csv(OUT / "propagation_events.csv", propagation)
    sampled = sorted(propagation, key=lambda row: (row["method"], row["condition"], row["instance_id"], row["event_index"]))
    rng = np.random.default_rng(AUDIT_SEED)
    examples = [sampled[index] for index in sorted(rng.choice(len(sampled), size=min(10, len(sampled)), replace=False).tolist())]
    write_csv(OUT / "propagation_examples.csv", examples)
    write_csv(OUT / "paired_contrasts.csv", contrasts)
    write_csv(OUT / "query_followup_counts.csv", query_counts())
    write_csv(OUT / "audit_sheet_cds.csv", audit_visible)
    write_csv(OUT / "audit_key_cds.csv", audit_key)
    costs = cost_scopes()
    (OUT / "cost_estimates.json").write_text(json.dumps(costs, indent=2, sort_keys=True) + "\n")
    report = ["# A9 CDS zero-call follow-up", "", "- A8 coverage: 416 unique episodes.",
              "- No model call or environment episode was run for this report.",
              "- Bootstrap: 10,000 independent-family draws, seed 20261017.", "",
              "## K+S boundary balanced-accuracy tests", ""]
    for row in paired_bia:
        report.append(f"- `{row['method']}` {row['comparison']}: {row['difference']:.6f} "
                      f"[95% CI {row['ci95_low']:.6f}, {row['ci95_high']:.6f}], n={row['n_pairs']}, "
                      f"significantly_greater={row['significantly_greater']}.")
    report.extend(("", "## Contact and propagation overlap", ""))
    for row in overlap_rows:
        report.append(f"- `{row['method']} / {row['condition']}`: intersection={row['intersection_n']}, "
                      f"union={row['union_n']}, contact_only={row['contact_only_n']}, "
                      f"propagation_only={row['propagation_only_n']}.")
    report.extend(("", "Propagation is a subset of contact by the fixed v3-d eligibility rule; overlap is therefore not independent behavioral evidence.",
                   "", "## Cost", ""))
    for row in costs["scopes"]:
        report.append(f"- `{row['scope']}`: {row['estimated_cost_cny_no_cache']:.6f} CNY uncached estimate; "
                      f"{row['estimated_cost_cny_all_input_cached_lower_bound']:.6f} CNY all-input-cached lower bound.")
    report.extend(("", "See CSV/JSON exports for all requested cells, trigger rules, and blinded audit rows."))
    (OUT / "REPORT.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"bia_pairs": len(paired_bia), "propagation_events": len(propagation),
                      "audit_rows": len(audit_visible), "contrasts": len(contrasts)}, sort_keys=True))


if __name__ == "__main__":
    main()
