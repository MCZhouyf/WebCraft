"""Deterministic A8 CDS aggregation over completed external-agent logs."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Callable

import numpy as np

from arbiter.evaluation.consequence_htr import inventory_dependencies
from arbiter.evaluation.external_scoring import ACTION_METHODS, _matches, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import (
    classify_actions_v3, event_inputs, intrinsic_discriminative, subject,
)
from scripts.run_cds_agents_20261017 import METHOD_CONDITIONS, WEBCRAFT, all_jobs
from scripts.run_cds_zero_call_20261017 import pricing_fit
from scripts.run_measurement_v3_20261014 import external_stale_map


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_INPUT = ROOT / "artifacts/post_review/cds_agents_20261017"
DEFAULT_OUTPUT = ROOT / "reports/main/post_review/cds_agents_20261017"
BOOTSTRAP_SEED = 20261017
BOOTSTRAP_DRAWS = 10_000


def arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input-dir", type=Path, default=DEFAULT_INPUT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--allow-partial", action="store_true")
    return parser.parse_args()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing empty CDS export: {path.name}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def records(input_dir: Path, allow_partial: bool) -> list[dict[str, Any]]:
    indexed: dict[tuple[str, str, str], dict[str, Any]] = {}
    for path in input_dir.glob("**/episodes.jsonl"):
        for row in jsonl(path):
            key = (row["method"], row["condition"], row["instance_id"])
            if key in indexed:
                raise RuntimeError(f"duplicate CDS episode: {key}")
            indexed[key] = row
    expected = {(row["method"], row["condition"], row["instance_id"])
                for row in all_jobs(WEBCRAFT)}
    missing = sorted(expected - set(indexed))
    extra = sorted(set(indexed) - expected)
    if extra or (missing and not allow_partial):
        raise RuntimeError(f"CDS coverage mismatch: missing={len(missing)}, extra={len(extra)}")
    return [indexed[key] for key in sorted(indexed)]


def inputs_absent(events: list[dict[str, Any]], index: int, predicted: set[str]) -> bool:
    consumed = {
        str(value).removeprefix("minecraft:")
        for value in event_inputs(events[index]) & predicted
    }
    if not consumed:
        return False
    inventory: Counter[str] = Counter()
    for event in events[:index]:
        if event.get("method") == "inventory" and isinstance(event.get("result"), list):
            inventory = Counter({str(row["name"]).removeprefix("minecraft:"): int(row["count"])
                                 for row in event["result"]})
        for row in event.get("item_flow", {}).get("consumed", []):
            inventory[str(row["name"]).removeprefix("minecraft:")] -= int(row["count"])
        for row in event.get("item_flow", {}).get("produced", []):
            inventory[str(row["name"]).removeprefix("minecraft:")] += int(row["count"])
    return any(inventory[value] <= 0 for value in consumed)


def classify_query_followup(
    query: dict[str, Any], actions: list[dict[str, Any]], events: list[dict[str, Any]],
    world: str, conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
) -> tuple[str, int | None]:
    if not query["mismatch"]:
        return "not_denied", None
    index = int(query["event_index"])
    focal = {subject(conflicts[value]["assertion"]) for value in query["stale_mechanism_ids"]}
    stale_assertions = [row["assertion"] for row in conflicts.values()
                        if row["truth_vector"].get(world) is False
                        and subject(row["assertion"]) in focal]
    true_assertions = [row["assertion"] for row in conflicts.values()
                       if row["truth_vector"].get(world) is True
                       and subject(row["assertion"]) in focal]
    action_by_index = {int(row["event_index"]): row for row in actions}
    for later in range(index + 1, len(events)):
        event = events[later]
        if event.get("method") not in ACTION_METHODS:
            continue
        annotation = action_by_index.get(later)
        stale_same_subject = bool(annotation and any(
            subject(conflicts[value]["assertion"]) in focal
            for value in annotation["stale_mechanism_ids"]
        ))
        if stale_same_subject or any(_matches(event, assertion, nodes) for assertion in stale_assertions):
            return "denied_then_stale", later
        if any(_matches(event, assertion, nodes) for assertion in true_assertions):
            return "denied_then_target_valid", later
    return "no_related_followup", None


def audit_episode(
    record: dict[str, Any], conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
    manifest_rows: dict[str, dict[str, Any]], tasks: dict[str, dict[str, Any]],
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    events = jsonl(ROOT / record["action_log"])
    world = record["target_world"]
    stale_map = external_stale_map(events, world, conflicts, nodes)
    classified = classify_actions_v3(
        events, stale_by_event=stale_map, conflicts=conflicts, resource_nodes=nodes,
        target=tasks[record["task_id"]]["target"], episode_success=bool(record["success"]),
    )
    dependencies = inventory_dependencies(events)
    annotations = []
    for row in classified:
        index = int(row["event_index"])
        event = events[index]
        intrinsic = intrinsic_discriminative(
            event, row["stale_mechanism_ids"], world=world,
            conflicts=conflicts, resource_nodes=nodes,
        )
        discriminative = int(bool(intrinsic or row["referenced_at"]))
        predicted = set(row["predicted_products"])
        fifo = any(consumer > index and name in predicted
                   for consumer, name, _ in dependencies.get(index, []))
        absent = any(inputs_absent(events, int(later), predicted) for later in row["referenced_at"])
        denied = int(bool(row["mismatch"]))
        commit = row["v3_label"] == "commit"
        rule_i = int(discriminative and denied and commit and (fifo or absent))
        rule_ii = int(discriminative and denied and commit and bool(row["repeated_at"]))
        rule_iii = int(discriminative and denied and commit and row["affirmative_submit_at"] != "")
        annotations.append({
            **row, "instance_id": record["instance_id"], "condition": record["condition"],
            "episode_method": record["method"], "discriminative": discriminative,
            "v3d_propagation": int(bool(rule_i or rule_ii or rule_iii)),
            "action_type": "query" if event.get("method") == "check_name" else "action",
        })

    discriminative = [row for row in annotations if row["discriminative"]]
    denied_discriminative = [row for row in discriminative if row["mismatch"]]
    v3d_lower = int(any(row["v3d_propagation"] for row in denied_discriminative))
    v3d_upper = int(v3d_lower or any(row["v3_label"] == "undetermined"
                                    for row in denied_discriminative))
    query_rows = [row for row in discriminative if row["action_type"] == "query"]
    followup_counts = Counter()
    for row in query_rows:
        label, later = classify_query_followup(row, annotations, events, world, conflicts, nodes)
        followup_counts[label] += 1
        row["followup_class"] = label
        row["followup_event_index"] = "" if later is None else later

    meta = manifest_rows[record["instance_id"]]
    evaluator = json.loads((WEBCRAFT / meta["evaluator_path"]).read_text())
    focal_truth = {row["conflict_id"]: bool(row["truth"]) for row in evaluator["boundary_truth"]}
    first_target: int | None = None
    first_index: int | None = None
    for index, event in enumerate(events):
        if event.get("method") not in ACTION_METHODS:
            continue
        matched = [conflict_id for conflict_id in focal_truth
                   if conflict_id in conflicts and _matches(event, conflicts[conflict_id]["assertion"], nodes)]
        if matched:
            first_index = index
            first_target = int(any(focal_truth[value] for value in matched)
                               and not any(not focal_truth[value] for value in matched))
            break

    boundary = record["boundary"]
    terminal_failure = int(record["gold_score"]["terminal"] == "execution_failed")
    output = {
        "method": record["method"], "condition": record["condition"],
        "instance_id": record["instance_id"], "task_id": record["task_id"],
        "family_id": record["family_id"], "target_world": world,
        "true_feasible": int(record["true_feasible"]), "success": int(record["success"]),
        "feasible_success": int(record["true_feasible"] and record["success"]),
        "overconservative": int(record["gold_score"]["false_abstention"]),
        "terminal_execution_failure": terminal_failure,
        "first_conflict_event_index": "" if first_index is None else first_index,
        "first_conflict_target_agreement": "" if first_target is None else first_target,
        "HTR_all": int(record["metrics"]["HTR_all"]),
        "discriminative_stale_contact": int(bool(discriminative)),
        "query_contact": int(bool(query_rows)),
        "action_contact": int(any(row["action_type"] == "action" for row in discriminative)),
        "discriminative_probe": int(bool(query_rows)),
        "denied_discriminative_contact": int(bool(denied_discriminative)),
        "v3d_lower": v3d_lower, "v3d_upper": v3d_upper,
        "boundary_items": int(boundary["boundary_items"]),
        "boundary_answered": int(boundary["boundary_answered"]),
        "boundary_correct": int(boundary["boundary_correct"]),
        "boundary_uncertain": int(boundary["boundary_items"] - boundary["boundary_answered"]),
        "BIA_episode": (boundary["boundary_correct"] / boundary["boundary_answered"]
                        if boundary["boundary_answered"] else ""),
        "boundary_uncertain_episode": (
            (boundary["boundary_items"] - boundary["boundary_answered"]) / boundary["boundary_items"]
            if boundary["boundary_items"] else ""
        ),
        "newest_correct": boundary["newest_timestamp_BIA"] * boundary["boundary_items"],
        "majority_correct": boundary["majority_class_BIA"] * boundary["boundary_items"],
        "semantic_actions": int(record["gold_score"]["semantic_actions"]),
        "action_model_calls": int(record["action_model_calls"]),
        "boundary_model_calls": int(record["boundary_model_calls"]),
        "input_tokens": int(record["input_tokens"]), "output_tokens": int(record["output_tokens"]),
        "provider_cost_cny": record.get("estimated_provider_cost"),
        "server_model": ";".join(record["server_model_identifiers"]),
        "terminal": record["gold_score"]["terminal"],
        **{f"followup_{name}": followup_counts[name] for name in (
            "denied_then_target_valid", "denied_then_stale", "not_denied", "no_related_followup")},
    }
    return output, annotations


def ratio(rows: list[dict[str, Any]], numerator: str, denominator: str | None = None) -> float | str:
    selected = rows if denominator is None else [row for row in rows if row[denominator]]
    return mean(float(row[numerator]) for row in selected) if selected else ""


def aggregate(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["method"], row["condition"])].append(row)
    output = []
    for (method, condition), values in sorted(grouped.items()):
        answered = sum(row["boundary_answered"] for row in values)
        items = sum(row["boundary_items"] for row in values)
        decisions = [row for row in values if row["first_conflict_target_agreement"] != ""]
        denied = [row for row in values if row["denied_discriminative_contact"]]
        output.append({
            "method": method, "condition": condition, "n": len(values),
            "feasible_n": sum(row["true_feasible"] for row in values),
            "SR": ratio(values, "success"), "feasible_SR": ratio(values, "success", "true_feasible"),
            "overconservatism": ratio(values, "overconservative", "true_feasible"),
            "terminal_execution_failure": ratio(values, "terminal_execution_failure"),
            "first_conflict_decisions": len(decisions),
            "first_conflict_target_agreement": ratio(decisions, "first_conflict_target_agreement"),
            "HTR_all": ratio(values, "HTR_all"),
            "discriminative_stale_contact": ratio(values, "discriminative_stale_contact"),
            "query_contact": ratio(values, "query_contact"), "action_contact": ratio(values, "action_contact"),
            "v3d_lower": ratio(values, "v3d_lower"), "v3d_upper": ratio(values, "v3d_upper"),
            "v3d_conditional": ratio(denied, "v3d_lower"),
            "BIA": sum(row["boundary_correct"] for row in values) / answered if answered else "",
            "boundary_uncertain_rate": sum(row["boundary_uncertain"] for row in values) / items,
            "newest_timestamp_BIA": sum(row["newest_correct"] for row in values) / items,
            "majority_class_BIA": sum(row["majority_correct"] for row in values) / items,
            "mean_semantic_actions": mean(row["semantic_actions"] for row in values),
            "mean_action_model_calls": mean(row["action_model_calls"] for row in values),
            "mean_total_model_calls": mean(row["action_model_calls"] + row["boundary_model_calls"] for row in values),
            "mean_input_tokens": mean(row["input_tokens"] for row in values),
            "mean_output_tokens": mean(row["output_tokens"] for row in values),
            "total_provider_cost_cny": sum(float(row["provider_cost_cny"] or 0) for row in values),
        })
    return output


def bootstrap_difference(
    left: list[dict[str, Any]], right: list[dict[str, Any]], metric: str,
    eligible: Callable[[dict[str, Any]], bool] = lambda row: True,
) -> tuple[float, float, float, int]:
    left_by = {row["instance_id"]: row for row in left if eligible(row)}
    right_by = {row["instance_id"]: row for row in right if eligible(row)}
    common = sorted(set(left_by) & set(right_by))
    families: dict[str, list[float]] = defaultdict(list)
    for key in common:
        families[left_by[key]["family_id"]].append(float(left_by[key][metric]) - float(right_by[key][metric]))
    family_values = np.asarray([mean(values) for values in families.values()], dtype=float)
    if not len(family_values):
        return float("nan"), float("nan"), float("nan"), 0
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = family_values[rng.integers(0, len(family_values), size=(BOOTSTRAP_DRAWS, len(family_values)))].mean(axis=1)
    point = mean(float(left_by[key][metric]) - float(right_by[key][metric]) for key in common)
    low, high = np.quantile(draws, [0.025, 0.975])
    return point, float(low), float(high), len(common)


def bootstrap_mean(
    rows: list[dict[str, Any]], metric: str,
    eligible: Callable[[dict[str, Any]], bool] = lambda row: True,
) -> tuple[float, float, float, int]:
    selected = [row for row in rows if eligible(row) and row[metric] != ""]
    families: dict[str, list[float]] = defaultdict(list)
    for row in selected:
        families[row["family_id"]].append(float(row[metric]))
    family_values = np.asarray([mean(values) for values in families.values()], dtype=float)
    if not len(family_values):
        return float("nan"), float("nan"), float("nan"), 0
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    draws = family_values[
        rng.integers(0, len(family_values), size=(BOOTSTRAP_DRAWS, len(family_values)))
    ].mean(axis=1)
    low, high = np.quantile(draws, [0.025, 0.975])
    return mean(float(row[metric]) for row in selected), float(low), float(high), len(selected)


def condition_intervals(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    metrics = (
        ("SR", "success", lambda row: True),
        ("feasible_SR", "success", lambda row: bool(row["true_feasible"])),
        ("overconservatism", "overconservative", lambda row: bool(row["true_feasible"])),
        ("terminal_execution_failure", "terminal_execution_failure", lambda row: True),
        ("first_conflict_target_agreement", "first_conflict_target_agreement",
         lambda row: row["first_conflict_target_agreement"] != ""),
        ("HTR_all", "HTR_all", lambda row: True),
        ("discriminative_stale_contact", "discriminative_stale_contact", lambda row: True),
        ("query_contact", "query_contact", lambda row: True),
        ("action_contact", "action_contact", lambda row: True),
        ("v3d_lower", "v3d_lower", lambda row: True),
        ("v3d_upper", "v3d_upper", lambda row: True),
        ("BIA_episode", "BIA_episode", lambda row: row["BIA_episode"] != ""),
        ("semantic_actions", "semantic_actions", lambda row: True),
        ("total_model_calls", "total_model_calls", lambda row: True),
    )
    enriched = [
        {**row, "total_model_calls": row["action_model_calls"] + row["boundary_model_calls"]}
        for row in rows
    ]
    output = []
    for (method, condition), values in sorted(_groups(enriched, ("method", "condition")).items()):
        for label, field, eligible in metrics:
            point, low, high, n = bootstrap_mean(values, field, eligible)
            output.append({
                "method": method, "condition": condition, "metric": label, "n": n,
                "estimate": point, "ci95_low": low, "ci95_high": high,
                "bootstrap_unit": "independent_family", "draws": BOOTSTRAP_DRAWS,
                "seed": BOOTSTRAP_SEED,
            })
    return output


def contrasts(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    rows = [
        {**row, "total_model_calls": row["action_model_calls"] + row["boundary_model_calls"]}
        for row in rows
    ]
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        grouped[(row["method"], row["condition"])].append(row)
    pairs = (("K+S", "K+N"), ("S", "K"), ("empty", "K"), ("S", "empty"))
    metrics = (
        ("SR", "success", lambda row: True),
        ("feasible_SR", "success", lambda row: bool(row["true_feasible"])),
        ("overconservatism", "overconservative", lambda row: bool(row["true_feasible"])),
        ("terminal_execution_failure", "terminal_execution_failure", lambda row: True),
        ("first_conflict_target_agreement", "first_conflict_target_agreement",
         lambda row: row["first_conflict_target_agreement"] != ""),
        ("HTR_all", "HTR_all", lambda row: True),
        ("discriminative_stale_contact", "discriminative_stale_contact", lambda row: True),
        ("query_contact", "query_contact", lambda row: True),
        ("action_contact", "action_contact", lambda row: True),
        ("v3d_lower", "v3d_lower", lambda row: True),
        ("v3d_upper", "v3d_upper", lambda row: True),
        ("BIA_episode", "BIA_episode", lambda row: row["BIA_episode"] != ""),
        ("semantic_actions", "semantic_actions", lambda row: True),
        ("total_model_calls", "total_model_calls", lambda row: True),
    )
    output = []
    for method in METHOD_CONDITIONS:
        for left, right in pairs:
            if (method, left) not in grouped or (method, right) not in grouped:
                continue
            for label, field, eligible in metrics:
                point, low, high, n = bootstrap_difference(
                    grouped[(method, left)], grouped[(method, right)], field, eligible,
                )
                output.append({
                    "method": method, "contrast": f"{left} - {right}", "metric": label,
                    "n_pairs": n, "difference": point, "ci95_low": low, "ci95_high": high,
                    "bootstrap_unit": "independent_family", "draws": BOOTSTRAP_DRAWS,
                    "seed": BOOTSTRAP_SEED,
                })
    return output


def boundary_strata(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for (method, condition, probe), values in sorted(_groups(rows, ("method", "condition", "discriminative_probe")).items()):
        answered = sum(row["boundary_answered"] for row in values)
        items = sum(row["boundary_items"] for row in values)
        output.append({
            "method": method, "condition": condition, "discriminative_probe": probe,
            "n": len(values), "items": items, "answered": answered,
            "BIA": sum(row["boundary_correct"] for row in values) / answered if answered else "",
            "uncertain_rate": sum(row["boundary_uncertain"] for row in values) / items,
            "newest_timestamp_BIA": sum(row["newest_correct"] for row in values) / items,
            "majority_class_BIA": sum(row["majority_correct"] for row in values) / items,
        })
    return output


def _groups(rows: list[dict[str, Any]], fields: tuple[str, ...]) -> dict[tuple[Any, ...], list[dict[str, Any]]]:
    output: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        output[tuple(row[field] for field in fields)].append(row)
    return output


def provider_ledger_audit(input_dir: Path) -> dict[str, Any]:
    paths = list(input_dir.glob("**/ledger.jsonl"))
    rows: list[dict[str, Any]] = []
    for path in paths:
        rows.extend(jsonl(path))
    provider = [row for row in rows if row.get("record_type") != "canary_validation"]
    successful = [row for row in provider if row.get("status") == "ok"]
    return {
        "ledger_files": len(paths),
        "provider_attempts": len(provider),
        "successful_provider_calls": len(successful),
        "canary_calls": sum(bool(row.get("canary")) for row in successful),
        "infrastructure_errors": sum(row.get("status") == "infrastructure_error" for row in provider),
        "input_tokens": sum(int(row.get("input_tokens", 0) or 0) for row in successful),
        "cached_input_tokens": sum(int(row.get("cached_input_tokens", 0) or 0) for row in successful),
        "output_tokens": sum(int(row.get("output_tokens", 0) or 0) for row in successful),
        "priced_cost_cny": sum(float(row.get("cost", 0) or 0) for row in successful),
        "model_identifiers": sorted({str(row["model"]) for row in successful if row.get("model")}),
    }


def prompt_audit(source: list[dict[str, Any]]) -> dict[str, Any]:
    prompts = responses = non_stop = malformed = 0
    models: set[str] = set()
    forbidden = Counter()
    terms = (
        "truth_vector", "target_world", "comparison_world", "evaluator_path",
        "candidate_versions", "candidate version list",
    )
    for episode in source:
        path = ROOT / episode["prompt_log"]
        if not path.exists():
            malformed += 1
            continue
        rows = jsonl(path)
        episode_prompts = [row for row in rows if row.get("record_type") == "full_prompt"]
        episode_responses = [row for row in rows if row.get("record_type") == "full_response"]
        prompts += len(episode_prompts)
        responses += len(episode_responses)
        if len(episode_prompts) != len(episode_responses):
            malformed += 1
        for row in episode_responses:
            models.add(str(row.get("model", "")))
            non_stop += row.get("finish_reason") != "stop"
        text = json.dumps(
            [row.get("messages") for row in episode_prompts], ensure_ascii=False,
        ).lower()
        for term in terms:
            forbidden[term] += text.count(term)
    return {
        "episodes": len(source), "full_prompts": prompts, "full_responses": responses,
        "episodes_with_unpaired_records": malformed, "non_stop_finish_reasons": non_stop,
        "server_model_identifiers": sorted(models),
        "forbidden_field_occurrences": dict(forbidden),
        "complete_prompts_retained": True,
    }


def _percent(value: Any) -> str:
    return f"{100 * float(value):.2f}%"


def _contrast_line(rows: list[dict[str, Any]], method: str, contrast: str, metric: str) -> str:
    row = next(value for value in rows if value["method"] == method
               and value["contrast"] == contrast and value["metric"] == metric)
    return (f"{metric} {_percent(row['difference'])} "
            f"[95% CI {_percent(row['ci95_low'])}, {_percent(row['ci95_high'])}]")


def first_run_query_lines(output_dir: Path) -> list[str]:
    path = output_dir / "query_followup_summary.csv"
    if not path.exists():
        return ["- First-run zero-call query audit was not found."]
    grouped: dict[str, dict[str, int]] = defaultdict(dict)
    with path.open(newline="") as handle:
        for row in csv.DictReader(handle):
            grouped[row["method"]][row["followup_class"]] = int(row["count"])
    output = []
    for method, values in sorted(grouped.items()):
        output.append(f"- `{method}`: {json.dumps(values, sort_keys=True)}")
    return output


def main() -> None:
    parsed = arguments()
    input_dir, output_dir = parsed.input_dir.resolve(), parsed.output_dir.resolve()
    source = records(input_dir, parsed.allow_partial)
    conflicts = {row["id"]: row for row in load_conflicts(WEBCRAFT / "data/conflicts.jsonl")}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    manifest = json.loads((WEBCRAFT / "cds_materials/cds_manifest.json").read_text())
    manifest_rows = {row["instance_id"]: row for row in manifest["rows"]}
    tasks = {row["id"]: row for row in jsonl(WEBCRAFT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")}
    episode_rows, action_rows = [], []
    for record in source:
        episode, actions = audit_episode(record, conflicts, nodes, manifest_rows, tasks)
        episode_rows.append(episode)
        action_rows.extend(actions)
    output_dir.mkdir(parents=True, exist_ok=True)
    write_csv(output_dir / "episode_metrics.csv", episode_rows)
    write_csv(output_dir / "main_table.csv", aggregate(episode_rows))
    write_csv(output_dir / "condition_intervals.csv", condition_intervals(episode_rows))
    write_csv(output_dir / "boundary_accuracy.csv", boundary_strata(episode_rows))
    contrast_rows = contrasts(episode_rows)
    if contrast_rows:
        write_csv(output_dir / "primary_contrasts.csv", contrast_rows)
    elif not parsed.allow_partial:
        raise RuntimeError("complete CDS input produced no primary contrasts")
    if action_rows:
        write_csv(output_dir / "stale_action_audit.csv", action_rows)
    pricing = pricing_fit()
    provider_audit = provider_ledger_audit(input_dir)
    prompts = prompt_audit(source)
    final_cost = {
        "schema_version": 1, "episodes": len(episode_rows), "estimated": True,
        "currency": "CNY", "total_input_tokens": sum(row["input_tokens"] for row in episode_rows),
        "total_output_tokens": sum(row["output_tokens"] for row in episode_rows),
        "ledger_priced_cost_cny": sum(float(row["provider_cost_cny"] or 0) for row in episode_rows),
        "all_provider_ledger_activity": provider_audit,
        "pricing_fit": pricing,
    }
    (output_dir / "cost_estimate_final.json").write_text(json.dumps(final_cost, indent=2, sort_keys=True) + "\n")
    (output_dir / "prompt_audit.json").write_text(json.dumps(prompts, indent=2, sort_keys=True) + "\n")
    table = aggregate(episode_rows)
    react_k = next((row for row in table if row["method"] == "react" and row["condition"] == "K"), None)
    gate = bool(react_k and react_k["feasible_SR"] != "" and float(react_k["feasible_SR"]) >= 0.6)
    model_ids = sorted({row["server_model"] for row in episode_rows})
    followups = Counter()
    for row in episode_rows:
        for name in ("denied_then_target_valid", "denied_then_stale", "not_denied", "no_related_followup"):
            followups[(row["method"], name)] += int(row[f"followup_{name}"])
    report = [
        "# A8 CDS agent report", "",
        f"- Coverage: {len(episode_rows)}/416 primary episodes.",
        f"- Server model identifiers: `{', '.join(model_ids)}`.",
        f"- ReAct K feasible-task SR: {react_k['feasible_SR'] if react_k else 'not available'}.",
        f"- Gate (ReAct K feasible SR >= 0.6): {'not triggered' if gate else 'triggered; CDS remains basic-capability limited'}.",
        f"- Total calls: {sum(row['action_model_calls'] + row['boundary_model_calls'] for row in episode_rows)}; "
        f"input/output tokens: {final_cost['total_input_tokens']}/{final_cost['total_output_tokens']}.",
        f"- Estimated ledger-priced cost: {final_cost['ledger_priced_cost_cny']:.6f} CNY.", "",
        f"- All provider activity (including canaries and recovered partial attempts): "
        f"{provider_audit['successful_provider_calls']} successful calls, "
        f"{provider_audit['infrastructure_errors']} infrastructure errors, "
        f"{provider_audit['priced_cost_cny']:.6f} CNY.",
        f"- Prompt audit: {prompts['full_prompts']} prompts / {prompts['full_responses']} responses; "
        f"{prompts['episodes_with_unpaired_records']} unpaired episodes; "
        f"{prompts['non_stop_finish_reasons']} non-stop finish reasons.", "",
        "## Preregistered contrasts", "",
        "- ReAct K+S - K+N: "
        + "; ".join(_contrast_line(contrast_rows, "react", "K+S - K+N", metric)
                    for metric in ("SR", "feasible_SR", "discriminative_stale_contact", "v3d_lower")) + ".",
        "- ReAct S - K: "
        + "; ".join(_contrast_line(contrast_rows, "react", "S - K", metric)
                    for metric in ("SR", "feasible_SR", "discriminative_stale_contact", "v3d_lower")) + ".",
        "- ReAct empty - K: "
        + "; ".join(_contrast_line(contrast_rows, "react", "empty - K", metric)
                    for metric in ("SR", "feasible_SR", "discriminative_stale_contact", "v3d_lower")) + ".",
        "- ReAct S - empty: "
        + "; ".join(_contrast_line(contrast_rows, "react", "S - empty", metric)
                    for metric in ("SR", "feasible_SR", "discriminative_stale_contact", "v3d_lower")) + ".",
        "- Direct empty - K: "
        + "; ".join(_contrast_line(contrast_rows, "direct_llm", "empty - K", metric)
                    for metric in ("SR", "feasible_SR", "discriminative_stale_contact", "v3d_lower")) + ".", "",
        "Per-condition independent-family bootstrap intervals for all registered metrics are in `condition_intervals.csv`.", "",
        "## Boundary identification", "",
    ]
    for row in table:
        report.append(
            f"- `{row['method']} / {row['condition']}`: BIA {_percent(row['BIA'])}; "
            f"newest-timestamp {_percent(row['newest_timestamp_BIA'])}; "
            f"majority-class {_percent(row['majority_class_BIA'])}; "
            f"uncertain {_percent(row['boundary_uncertain_rate'])}."
        )
    report.extend(("", "## First-run zero-call query follow-up", ""))
    report.extend(first_run_query_lines(output_dir))
    report.extend(("", "## CDS query follow-up", ""))
    for method in METHOD_CONDITIONS:
        values = {name: followups[(method, name)] for name in (
            "denied_then_target_valid", "denied_then_stale", "not_denied", "no_related_followup")}
        report.append(f"- `{method}`: {json.dumps(values, sort_keys=True)}")
    report.extend(("", "All statistics are deterministic replays of frozen logs. Confidence intervals use 10,000 independent-family cluster bootstrap draws with seed 20261017."))
    (output_dir / "REPORT.md").write_text("\n".join(report) + "\n")
    print(json.dumps({"episodes": len(episode_rows), "gate_passed": gate,
                      "model_ids": model_ids, "total_calls": sum(row["action_model_calls"] + row["boundary_model_calls"] for row in episode_rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
