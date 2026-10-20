"""Run preregistered A4 zero-call measurement follow-up."""
from __future__ import annotations

import csv
import hashlib
import itertools
import json
import sys
from collections import Counter, defaultdict, deque
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from arbiter.evaluation.consequence_htr import action_signature, inventory_dependencies
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import event_inputs, item, predicted_products
from scripts.run_measurement_v3_20261014 import (
    EXTERNAL_PATHS, ROOT, WEBCRAFT, csv_rows, external_sets, group, jsonl, mean,
    pipeline_datasets, task_index, terminal_label, write_csv,
)


OUT = ROOT / "reports/main/post_review/measurement_v3_followup"
M3 = ROOT / "reports/main/post_review/measurement_v3"
PREREG = ROOT / "prereg/measurement_v3_followup_20261015.md"
RNG_SEED = 20261015
DRAWS = 10_000
RATE_THRESHOLD = 0.15
ACTION_THRESHOLD = 0.15
PIPELINE_METHODS = (
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "oracle",
    "arbiter", "probe_then_rag",
)
ACTION_PAIRS = (
    ("naive_rag", "verify_all"),
    ("parametric", "probe_then_parametric"),
    ("naive_rag", "arbiter_shell_naive"),
    ("naive_rag", "arbiter"),
)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def semantic_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in record["events"] if event.get("method") in ACTION_METHODS]


def public_result(event: dict[str, Any]) -> dict[str, Any]:
    result = event.get("result")
    if not isinstance(result, dict):
        return {"value": result}
    result = dict(result)
    result.pop("cost", None)
    return result


def action_parts(event: dict[str, Any]) -> tuple[str, str, str, str]:
    return (
        str(event.get("method")), canonical(event.get("params", {})),
        canonical({"success": event.get("success"), "result": public_result(event)}),
        canonical(event.get("cost", {})),
    )


def action_sequence(record: dict[str, Any]) -> list[tuple[str, str, str, str]]:
    return [action_parts(event) for event in semantic_events(record)]


def dedupe_pipeline_records(
    datasets: dict[str, list[dict[str, Any]]],
) -> tuple[dict[tuple[str, str, str, str, str], dict[str, Any]], list[str]]:
    output = {}
    notes = []
    for mode, records in datasets.items():
        grouped = group(records, "method", "condition", "world", "task_id")
        for key, values in grouped.items():
            sequences = [action_sequence(row) for row in values]
            if any(sequence != sequences[0] for sequence in sequences[1:]):
                raise RuntimeError(f"seed action sequence differs: {mode}/{key}")
            selected = next((row for row in values if int(row["seed"]) == 20261006), values[0])
            output[(mode, key[0], key[1], key[2], key[3])] = selected
        notes.append(f"{mode}: {len(grouped)} method-instance keys seed-checked")
    return output, notes


def first_difference(
    left: list[tuple[str, str, str, str]], right: list[tuple[str, str, str, str]],
) -> tuple[int | str, str, str, str]:
    for index in range(min(len(left), len(right))):
        if left[index] == right[index]:
            continue
        lrow, rrow = left[index], right[index]
        if lrow[0] != rrow[0]:
            kind = "order_different"
        elif lrow[1] != rrow[1]:
            kind = "parameter_different"
        elif lrow[2] != rrow[2]:
            kind = "result_different"
        else:
            kind = "cost_different"
        return index, kind, canonical(lrow), canonical(rrow)
    if len(left) > len(right):
        return len(right), "left_extra_action", canonical(left[len(right)]), ""
    if len(right) > len(left):
        return len(left), "right_extra_action", "", canonical(right[len(left)])
    return "", "identical", "", ""


def action_identity(
    records: dict[tuple[str, str, str, str, str], dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    details = []
    probes = []
    for mode in ("all_public", "llm_only", "oracle_reading"):
        for left_method, right_method in ACTION_PAIRS:
            left = {
                (condition, world, task): record
                for (current_mode, method, condition, world, task), record in records.items()
                if current_mode == mode and method == left_method
            }
            right = {
                (condition, world, task): record
                for (current_mode, method, condition, world, task), record in records.items()
                if current_mode == mode and method == right_method
            }
            if set(left) != set(right) or len(left) != 374:
                raise RuntimeError(f"identity matrix mismatch: {mode}/{left_method}/{right_method}")
            for key in sorted(left):
                lseq, rseq = action_sequence(left[key]), action_sequence(right[key])
                position, kind, lvalue, rvalue = first_difference(lseq, rseq)
                details.append({
                    "reading_mode": mode, "left_method": left_method, "right_method": right_method,
                    "condition": key[0], "world": key[1], "task_id": key[2],
                    "left_actions": len(lseq), "right_actions": len(rseq),
                    "right_minus_left_actions": len(rseq) - len(lseq),
                    "identical": int(kind == "identical"), "first_difference_position": position,
                    "first_difference_type": kind, "left_action": lvalue, "right_action": rvalue,
                })
                if left_method == "naive_rag" and right_method == "verify_all":
                    naive_events = semantic_events(left[key])
                    verify_events = semantic_events(right[key])
                    for semantic_index, event in enumerate(verify_events):
                        if event.get("intent") != "probe":
                            continue
                        same = semantic_index < len(naive_events) and action_parts(event)[:2] == action_parts(naive_events[semantic_index])[:2]
                        probes.append({
                            "reading_mode": mode, "condition": key[0], "world": key[1], "task_id": key[2],
                            "semantic_index": semantic_index, "method": event.get("method"),
                            "params": canonical(event.get("params", {})),
                            "naive_same_position_and_params": int(same),
                        })
    summaries = []
    for (mode, left, right), rows in sorted(group(details, "reading_mode", "left_method", "right_method").items()):
        distribution = Counter(int(row["right_minus_left_actions"]) for row in rows)
        summaries.append({
            "reading_mode": mode, "left_method": left, "right_method": right,
            "instances": len(rows), "identical_instances": sum(row["identical"] for row in rows),
            "identity_rate": mean(row["identical"] for row in rows),
            "action_count_difference_distribution": canonical(dict(sorted(distribution.items()))),
            "label_difference_has_behavior_difference": int(
                left == "naive_rag" and right == "verify_all" and any(not row["identical"] for row in rows)
            ) if (left, right) == ("naive_rag", "verify_all") else "not_applicable",
        })
    return summaries, details, probes


def semantic_positions(record: dict[str, Any]) -> list[int]:
    return [index for index, event in enumerate(record["events"]) if event.get("method") in ACTION_METHODS]


def action_dependency(
    events: list[dict[str, Any]], event_index: int, predicted: set[str],
) -> tuple[int, int, str, str]:
    later = [
        index for index in range(event_index + 1, len(events))
        if events[index].get("method") in ACTION_METHODS
    ]
    references = [index for index in later if event_inputs(events[index]) & predicted]
    dependencies = inventory_dependencies(events).get(event_index, [])
    causal = [
        (consumer, name, count) for consumer, name, count in dependencies
        if name in predicted and consumer > event_index
    ]
    if causal:
        source = "stale_action_fifo_lot"
    elif references:
        source = "same_name_other_source"
    else:
        source = "no_reference"
    return (
        int(bool(references)), int(bool(causal)), source,
        canonical({"references": references, "causal_dependencies": causal}),
    )


def negative_dependencies(
    records: dict[tuple[str, str, str, str, str], dict[str, Any]],
    conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = [
        row for row in csv_rows(M3 / "verify_all_unique.csv")
        if row["next_behavior"] == "continue_original_plan"
    ]
    if len(source) != 69:
        raise RuntimeError(f"expected 69 unique continue events, found {len(source)}")
    stale_index = {
        (row["reading_mode"], row["condition"], row["world"], row["task_id"],
         int(row["seed"]), int(row["event_index"])): json.loads(row["stale_mechanism_ids"])
        for row in csv_rows(M3 / "pipeline_actions_v3.csv")
        if row["method"] == "verify_all"
    }
    output = []
    for row in source:
        mode, condition, world, task = (
            row["reading_mode"], row["condition"], row["world"], row["task_id"],
        )
        verify = records[(mode, "verify_all", condition, world, task)]
        naive = records[(mode, "naive_rag", condition, world, task)]
        verify_index = int(row["event_index"])
        mechanism = row["mechanism_id"]
        stale_ids = stale_index.get(
            (mode, condition, world, task, int(row["retained_seed"]), verify_index), [],
        )
        verify_semantic = semantic_positions(verify)
        semantic_index = verify_semantic.index(verify_index)
        trace = verify.get("policy_trace", [])[semantic_index]
        try:
            trace_signature = json.loads(trace.get("action_signature", "null"))
        except json.JSONDecodeError:
            trace_signature = None
        predicted = {
            str(item(trace_signature[2]))
        } if isinstance(trace_signature, list) and len(trace_signature) >= 3 and isinstance(trace_signature[2], str) else set()
        if not predicted and stale_ids:
            predicted = set().union(*(predicted_products(conflicts[value]["assertion"]) for value in stale_ids))
        v_name, v_causal, v_source, v_trace = action_dependency(
            verify["events"], verify_index, predicted,
        )
        naive_semantic = semantic_positions(naive)
        naive_index: int | None = None
        if semantic_index < len(naive_semantic):
            candidate = naive_semantic[semantic_index]
            if action_signature(naive["events"][candidate], nodes) == action_signature(verify["events"][verify_index], nodes):
                naive_index = candidate
        if naive_index is None:
            n_name, n_causal, n_source, n_trace = 0, 0, "action_absent", canonical({})
        else:
            n_name, n_causal, n_source, n_trace = action_dependency(
                naive["events"], naive_index, predicted,
            )
        output.append({
            "reading_mode": mode, "condition": condition, "world": world, "task_id": task,
            "retained_seed": row["retained_seed"], "mechanism_id": mechanism,
            "catalogue_stale_mechanism_ids": canonical(stale_ids),
            "predicted_products": canonical(sorted(predicted)),
            "verify_event_index": verify_index, "naive_event_index": "" if naive_index is None else naive_index,
            "legacy_naive_consumed_predicted_product": row["naive_consumed_predicted_product"],
            "verify_name_reference": v_name, "verify_causal_lot_dependency": v_causal,
            "verify_source_class": v_source, "verify_trace": v_trace,
            "naive_name_reference": n_name, "naive_causal_lot_dependency": n_causal,
            "naive_source_class": n_source, "naive_trace": n_trace,
            "legacy_difference_explanation": (
                "legacy name-only match came from initial inventory or another producer"
                if int(row["naive_consumed_predicted_product"]) and not n_causal else
                "legacy and FIFO causal-lot rules agree"
            ),
        })
    summaries = []
    for method in ("verify_all", "naive_rag"):
        prefix = "verify" if method == "verify_all" else "naive"
        summaries.append({
            "method": method, "unique_negative_events": len(output),
            "name_reference_events": sum(int(row[f"{prefix}_name_reference"]) for row in output),
            "causal_lot_dependency_events": sum(int(row[f"{prefix}_causal_lot_dependency"]) for row in output),
            "same_name_other_source_events": sum(row[f"{prefix}_source_class"] == "same_name_other_source" for row in output),
            "action_absent_events": sum(row[f"{prefix}_source_class"] == "action_absent" for row in output),
        })
    return output, summaries


def legal_format(event: dict[str, Any]) -> bool:
    method, params = event.get("method"), event.get("params", {})
    if not isinstance(params, dict):
        return False
    if method == "mine":
        return isinstance(params.get("nodeId"), str) and bool(params["nodeId"])
    if method == "craft_grid":
        layout = params.get("layout")
        return isinstance(layout, list) and len(layout) == 9 and all(
            value is None or isinstance(value, str) for value in layout
        )
    if method == "smelt":
        return isinstance(params.get("input"), str) and bool(params["input"]) and (
            "fuel" not in params or isinstance(params.get("fuel"), str)
        )
    if method == "check_name":
        return isinstance(params.get("name"), str) and bool(params["name"])
    if method == "submit":
        return isinstance(params.get("result"), dict)
    return False


def ratio_ci(
    task_counts: dict[str, tuple[float, float]], seed_offset: int = 0,
) -> tuple[float, float, int]:
    tasks = sorted(task_counts)
    numerators = np.array([task_counts[task][0] for task in tasks], dtype=float)
    denominators = np.array([task_counts[task][1] for task in tasks], dtype=float)
    rng = np.random.default_rng(RNG_SEED + seed_offset)
    values = []
    omitted = 0
    for start in range(0, DRAWS, 500):
        count = min(500, DRAWS - start)
        indices = rng.integers(0, len(tasks), size=(count, len(tasks)))
        nsum = numerators[indices].sum(axis=1)
        dsum = denominators[indices].sum(axis=1)
        valid = dsum > 0
        values.extend((nsum[valid] / dsum[valid]).tolist())
        omitted += int((~valid).sum())
    if not values:
        return float("nan"), float("nan"), omitted
    low, high = np.quantile(values, [0.025, 0.975])
    return float(low), float(high), omitted


def ratio_difference_ci(
    left: dict[str, tuple[float, float]], right: dict[str, tuple[float, float]], seed_offset: int = 0,
) -> tuple[float, float, int]:
    tasks = sorted(set(left) & set(right))
    arrays = [
        np.array([source[task][position] for task in tasks], dtype=float)
        for source in (left, right) for position in (0, 1)
    ]
    ln, ld, rn, rd = arrays
    rng = np.random.default_rng(RNG_SEED + seed_offset)
    values = []
    omitted = 0
    for start in range(0, DRAWS, 500):
        count = min(500, DRAWS - start)
        indices = rng.integers(0, len(tasks), size=(count, len(tasks)))
        left_n, left_d = ln[indices].sum(axis=1), ld[indices].sum(axis=1)
        right_n, right_d = rn[indices].sum(axis=1), rd[indices].sum(axis=1)
        valid = (left_d > 0) & (right_d > 0)
        values.extend(((left_n[valid] / left_d[valid]) - (right_n[valid] / right_d[valid])).tolist())
        omitted += int((~valid).sum())
    low, high = np.quantile(values, [0.025, 0.975])
    return float(low), float(high), omitted


def external_propagation(
    external_records: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]], str]:
    actions = [
        row for row in csv_rows(M3 / "external_actions_v3.csv")
        if row["setting"] == "first_run"
    ]
    episodes = [
        row for row in csv_rows(M3 / "external_episodes_v3.csv")
        if row["setting"] == "first_run"
    ]
    raw = {
        (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"])): row
        for row in external_records["first_run"]
    }
    event_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    annotated = []
    for row in actions:
        key = (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))
        events = event_cache.setdefault(key, jsonl(ROOT / raw[key]["action_log"]))
        witnesses = json.loads(row["commit_witnesses"])
        references = json.loads(row["referenced_at"])
        repeats = json.loads(row["repeated_at"])
        affirmative = row["affirmative_submit_at"]
        trigger_indices = {
            "rule_i": references[:1],
            "rule_ii": repeats[:1],
            "rule_iii": [] if affirmative == "" else [int(affirmative)],
        }
        legal_by_rule = {
            name: int(bool(indices) and legal_format(events[int(indices[0])]))
            for name, indices in trigger_indices.items()
        }
        commit = row["v3_label"] == "commit" and int(row["mismatch"]) == 1
        legal_commit = commit and any(legal_by_rule.values())
        annotated.append({
            **row, "world_denied": int(row["mismatch"]),
            "propagation_commit": int(commit), "legal_format_commit": int(legal_commit),
            "rule_i": int("predicted_product_referenced" in witnesses),
            "rule_ii": int("exact_stale_action_repeated" in witnesses),
            "rule_iii": int("affirmative_submit_without_route_change" in witnesses),
            **{f"{name}_legal": value for name, value in legal_by_rule.items()},
        })
    result = []
    for method, rows in sorted(group(annotated, "method").items()):
        denied = [row for row in rows if int(row["world_denied"])]
        task_conditional = {
            task: (
                sum(int(row["propagation_commit"]) for row in values if int(row["world_denied"])),
                sum(int(row["world_denied"]) for row in values),
            ) for task, values in group(rows, "task_id").items()
        }
        task_legal = {
            task: (
                sum(int(row["legal_format_commit"]) for row in values if int(row["world_denied"])),
                sum(int(row["world_denied"]) for row in values),
            ) for task, values in group(rows, "task_id").items()
        }
        episode_method = [row for row in episodes if row["method"] == method]
        lower_counts = {
            task: (sum(int(row["HTR_commit_c_v3_lower"]) for row in values), len(values))
            for task, values in group(episode_method, "task_id").items()
        }
        upper_counts = {
            task: (sum(int(row["HTR_commit_c_v3_upper"]) for row in values), len(values))
            for task, values in group(episode_method, "task_id").items()
        }
        c_low, c_high, c_omit = ratio_ci(task_conditional, 1)
        l_low, l_high, l_omit = ratio_ci(lower_counts, 2)
        u_low, u_high, u_omit = ratio_ci(upper_counts, 3)
        legal_low, legal_high, legal_omit = ratio_ci(task_legal, 4)
        result.append({
            "method": method, "episodes": len(episode_method), "stale_actions": len(rows),
            "world_denied_stale_actions": len(denied),
            "propagation_events": sum(int(row["propagation_commit"]) for row in denied),
            "conditional_propagation_rate": mean(int(row["propagation_commit"]) for row in denied) if denied else "",
            "conditional_ci_low": c_low, "conditional_ci_high": c_high,
            "episode_lower": mean(int(row["HTR_commit_c_v3_lower"]) for row in episode_method),
            "episode_lower_ci_low": l_low, "episode_lower_ci_high": l_high,
            "episode_upper": mean(int(row["HTR_commit_c_v3_upper"]) for row in episode_method),
            "episode_upper_ci_low": u_low, "episode_upper_ci_high": u_high,
            "rule_i_events": sum(int(row["rule_i"]) for row in denied),
            "rule_ii_events": sum(int(row["rule_ii"]) for row in denied),
            "rule_iii_events": sum(int(row["rule_iii"]) for row in denied),
            "legal_format_propagation_events": sum(int(row["legal_format_commit"]) for row in denied),
            "legal_format_propagation_rate": mean(int(row["legal_format_commit"]) for row in denied) if denied else "",
            "legal_ci_low": legal_low, "legal_ci_high": legal_high,
            "omitted_bootstrap_draws": c_omit + l_omit + u_omit + legal_omit,
        })

    condition_rows = []
    for method, rows in sorted(group(annotated, "method").items()):
        method_episodes = [row for row in episodes if row["method"] == method]
        split_actions = {
            "matched": [row for row in rows if row["condition"].startswith("matched_")],
            "mismatched": [row for row in rows if row["condition"].startswith("mismatched_")],
        }
        split_episodes = {
            "matched": [row for row in method_episodes if row["condition"].startswith("matched_")],
            "mismatched": [row for row in method_episodes if row["condition"].startswith("mismatched_")],
        }
        metrics_by_split: dict[str, dict[str, dict[str, tuple[float, float]]]] = {}
        for split in ("matched", "mismatched"):
            action_groups = group(split_actions[split], "task_id")
            episode_groups = group(split_episodes[split], "task_id")
            metrics_by_split[split] = {
                "conditional": {
                    task: (
                        sum(int(row["propagation_commit"]) for row in values if int(row["world_denied"])),
                        sum(int(row["world_denied"]) for row in values),
                    ) for task, values in action_groups.items()
                },
                "lower": {
                    task: (sum(int(row["HTR_commit_c_v3_lower"]) for row in values), len(values))
                    for task, values in episode_groups.items()
                },
                "upper": {
                    task: (sum(int(row["HTR_commit_c_v3_upper"]) for row in values), len(values))
                    for task, values in episode_groups.items()
                },
            }
        for metric in ("conditional", "lower", "upper"):
            matched = metrics_by_split["matched"][metric]
            mismatched = metrics_by_split["mismatched"][metric]
            matched_num = sum(value[0] for value in matched.values())
            matched_den = sum(value[1] for value in matched.values())
            mismatch_num = sum(value[0] for value in mismatched.values())
            mismatch_den = sum(value[1] for value in mismatched.values())
            low, high, omitted = ratio_difference_ci(mismatched, matched, 20 + len(condition_rows))
            condition_rows.append({
                "method": method, "metric": metric,
                "matched_numerator": matched_num, "matched_denominator": matched_den,
                "matched_rate": matched_num / matched_den if matched_den else "",
                "mismatched_numerator": mismatch_num, "mismatched_denominator": mismatch_den,
                "mismatched_rate": mismatch_num / mismatch_den if mismatch_den else "",
                "mismatched_minus_matched": (
                    mismatch_num / mismatch_den - matched_num / matched_den
                    if matched_den and mismatch_den else ""
                ),
                "difference_ci_low": low, "difference_ci_high": high,
                "omitted_bootstrap_draws": omitted,
            })

    witnesses_out = []
    for rule, flag, index_field in (
        ("rule_i", "rule_i", "referenced_at"),
        ("rule_ii", "rule_ii", "repeated_at"),
        ("rule_iii", "rule_iii", "affirmative_submit_at"),
    ):
        candidates = sorted(
            [row for row in annotated if int(row[flag]) and int(row["world_denied"])],
            key=lambda row: (row["method"], row["condition"], row["world"], row["task_id"], int(row["event_index"])),
        )[:3]
        for row in candidates:
            key = (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))
            events = event_cache[key]
            stale_index = int(row["event_index"])
            raw_indices = json.loads(row[index_field]) if index_field != "affirmative_submit_at" else [int(row[index_field])]
            later_index = int(raw_indices[0])
            witnesses_out.append({
                "rule": rule, "agent_method": row["method"], "condition": row["condition"],
                "world": row["world"], "task_id": row["task_id"],
                "stale_event_index": stale_index, "stale_method": events[stale_index].get("method"),
                "stale_action": canonical(events[stale_index].get("params", {})),
                "predicted_products": row["predicted_products"],
                "world_feedback": canonical(public_result(events[stale_index])),
                "later_event_index": later_index, "later_method": events[later_index].get("method"),
                "later_action": canonical(events[later_index].get("params", {})),
                "later_legal_format": int(legal_format(events[later_index])),
            })
    qwen_status = "absent: no completed second_family_qwen export existed before preregistration"
    event_rows = [{
        "method": row["method"], "condition": row["condition"], "world": row["world"],
        "task_id": row["task_id"], "seed": row["seed"], "event_index": row["event_index"],
        "action_method": row["action_method"], "stale_mechanism_ids": row["stale_mechanism_ids"],
        "commit_witnesses": row["commit_witnesses"],
        "rule_i": row["rule_i"], "rule_i_legal": row["rule_i_legal"],
        "rule_ii": row["rule_ii"], "rule_ii_legal": row["rule_ii_legal"],
        "rule_iii": row["rule_iii"], "rule_iii_legal": row["rule_iii_legal"],
        "dependent_action_legal_format": row["legal_format_commit"],
    } for row in annotated if int(row["propagation_commit"])]
    return result, condition_rows, event_rows, witnesses_out, qwen_status


def terminal_six(record: dict[str, Any], events: list[dict[str, Any]]) -> str:
    metrics = record.get("metrics") or {}
    success = bool(metrics.get("SR", record.get("success", False)))
    feasible = bool(record["true_feasible"])
    if success and feasible:
        return "solved"
    if success and not feasible:
        return "correct_infeasible"
    if record.get("reported_feasible") is False and feasible:
        return "false_infeasible"
    if record.get("reported_feasible") is False and not feasible:
        return "correct_infeasible"
    primary = float(metrics.get("primary_cost", record.get("primary_cost", 0)) or 0)
    terminal = str(record.get("terminal", record.get("terminal_runtime", "")))
    if primary >= 90 or "budget" in terminal:
        return "budget_exhausted"
    semantic = [event for event in events if event.get("method") in ACTION_METHODS]
    if semantic and semantic[-1].get("success") is False:
        return "execution_failed"
    return "plan_not_found"


def pipeline_behavior_rows(
    records: dict[tuple[str, str, str, str, str], dict[str, Any]],
) -> list[dict[str, Any]]:
    m3 = {
        (row["reading_mode"], row["method"], row["condition"], row["world"], row["task_id"]): row
        for row in csv_rows(M3 / "pipeline_episodes_v3.csv") if int(row["seed"]) == 20261006
    }
    output = []
    for key, record in records.items():
        mode, method, condition, world, task = key
        row = m3[key]
        output.append({
            "scope": "pipeline", "reading_mode": mode, "method": method,
            "condition": condition, "world": world, "task_id": task,
            "SR": int(record["metrics"]["SR"]),
            "discriminative_HTR_all": int(row["HTR_all_discriminative"]),
            "v3_lower": int(row["HTR_commit_c_v3_lower"]),
            "v3_upper": int(row["HTR_commit_c_v3_upper"]),
            "terminal": terminal_six(record, record["events"]),
            "semantic_actions": int(row["semantic_actions"]),
            "v2_HTR_all": int(row["HTR_all"]),
            "v2_HTR_commit": int(row["HTR_commit_plan_source"]),
            "v2_probe_count": float(record.get("probe_count", record["metrics"].get("probe_count", 0)) or 0),
            "v2_probe_cost": float(record.get("probe_cost", record["metrics"].get("probe_cost", 0)) or 0),
            "true_feasible": int(record["true_feasible"]),
        })
    return output


def external_behavior_rows(external_records: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    m3 = {
        (row["method"], row["condition"], row["world"], row["task_id"]): row
        for row in csv_rows(M3 / "external_episodes_v3.csv") if row["setting"] == "first_run"
    }
    output = []
    for record in external_records["first_run"]:
        key = (record["method"], record["condition"], record["world"], record["task_id"])
        row = m3[key]
        events = jsonl(ROOT / record["action_log"])
        metrics = record.get("metrics") or {}
        output.append({
            "scope": "external", "reading_mode": "all_public", "method": record["method"],
            "condition": record["condition"], "world": record["world"], "task_id": record["task_id"],
            "SR": int(metrics.get("SR", record.get("success", False))),
            "discriminative_HTR_all": int(row["HTR_all_discriminative"]),
            "v3_lower": int(row["HTR_commit_c_v3_lower"]),
            "v3_upper": int(row["HTR_commit_c_v3_upper"]),
            "terminal": terminal_six(record, events),
            "semantic_actions": int(row["semantic_actions"]),
            "v2_HTR_all": int(row["HTR_all"]),
            "v2_HTR_commit": int(row["legacy_self_reported_HTR_commit"]),
            "v2_probe_count": float(metrics.get("probe_count", 0) or 0),
            "v2_probe_cost": float(metrics.get("probe_cost", 0) or 0),
            "true_feasible": int(record["true_feasible"]),
        })
    return output


def always_abstain_rows(base: list[dict[str, Any]], scope: str, mode: str) -> list[dict[str, Any]]:
    return [{
        "scope": scope, "reading_mode": mode, "method": "always_abstain",
        "condition": row["condition"], "world": row["world"], "task_id": row["task_id"],
        "SR": int(not int(row["true_feasible"])), "discriminative_HTR_all": 0,
        "v3_lower": 0, "v3_upper": 0,
        "terminal": "false_infeasible" if int(row["true_feasible"]) else "correct_infeasible",
        "semantic_actions": 0, "v2_HTR_all": 0, "v2_HTR_commit": 0,
        "v2_probe_count": 0.0, "v2_probe_cost": 0.0,
        "true_feasible": int(row["true_feasible"]),
    } for row in base]


def scalar_difference_ci(
    left: list[dict[str, Any]], right: list[dict[str, Any]], metric: str, seed_offset: int,
) -> tuple[float, float, float]:
    key_fields = ("condition", "world", "task_id")
    lindex = {tuple(row[name] for name in key_fields): float(row[metric]) for row in left}
    rindex = {tuple(row[name] for name in key_fields): float(row[metric]) for row in right}
    if set(lindex) != set(rindex):
        raise RuntimeError("paired scalar keys differ")
    tasks = sorted({key[2] for key in lindex})
    lsum = np.array([sum(lindex[key] for key in lindex if key[2] == task) for task in tasks])
    rsum = np.array([sum(rindex[key] for key in rindex if key[2] == task) for task in tasks])
    counts = np.array([sum(key[2] == task for key in lindex) for task in tasks])
    rng = np.random.default_rng(RNG_SEED + seed_offset)
    values = []
    for start in range(0, DRAWS, 500):
        count = min(500, DRAWS - start)
        indices = rng.integers(0, len(tasks), size=(count, len(tasks)))
        denominator = counts[indices].sum(axis=1)
        values.extend(((lsum[indices].sum(axis=1) - rsum[indices].sum(axis=1)) / denominator).tolist())
    low, high = np.quantile(values, [0.025, 0.975])
    return mean(lindex.values()) - mean(rindex.values()), float(low), float(high)


TERMINALS = (
    "solved", "correct_infeasible", "false_infeasible", "budget_exhausted",
    "execution_failed", "plan_not_found",
)


def terminal_permutation(
    left: list[dict[str, Any]], right: list[dict[str, Any]], seed_offset: int,
) -> tuple[float, float]:
    key_fields = ("condition", "world", "task_id")
    lindex = {tuple(row[name] for name in key_fields): row["terminal"] for row in left}
    rindex = {tuple(row[name] for name in key_fields): row["terminal"] for row in right}
    if set(lindex) != set(rindex):
        raise RuntimeError("paired terminal keys differ")
    tasks = sorted({key[2] for key in lindex})
    left_counts = np.array([
        [sum(lindex[key] == terminal for key in lindex if key[2] == task) for terminal in TERMINALS]
        for task in tasks
    ], dtype=float)
    right_counts = np.array([
        [sum(rindex[key] == terminal for key in rindex if key[2] == task) for terminal in TERMINALS]
        for task in tasks
    ], dtype=float)
    total = len(lindex)
    observed = 0.5 * np.abs(left_counts.sum(axis=0) / total - right_counts.sum(axis=0) / total).sum()
    rng = np.random.default_rng(RNG_SEED + seed_offset)
    exceed = 0
    for start in range(0, DRAWS, 500):
        count = min(500, DRAWS - start)
        swap = rng.integers(0, 2, size=(count, len(tasks), 1))
        perm_left = np.where(swap, right_counts, left_counts).sum(axis=1)
        perm_right = np.where(swap, left_counts, right_counts).sum(axis=1)
        stats = 0.5 * np.abs(perm_left / total - perm_right / total).sum(axis=1)
        exceed += int((stats >= observed - 1e-15).sum())
    return float(observed), (exceed + 1) / (DRAWS + 1)


def pair_audit(
    scope: str, mode: str, methods: dict[str, list[dict[str, Any]]], seed_base: int,
) -> list[dict[str, Any]]:
    output = []
    behavior_metrics = (
        "discriminative_HTR_all", "v3_lower", "v3_upper", "semantic_actions",
    )
    v2_metrics = (
        "v2_HTR_all", "v2_HTR_commit", "v2_probe_count", "v2_probe_cost", "semantic_actions",
    )
    for pair_index, (left_name, right_name) in enumerate(itertools.combinations(sorted(methods), 2)):
        left, right = methods[left_name], methods[right_name]
        sr_diff, sr_low, sr_high = scalar_difference_ci(left, right, "SR", seed_base + pair_index * 100)
        sr_indistinguishable = sr_low <= 0 <= sr_high
        behavior_pass = []
        v2_pass = []
        metric_values: dict[str, tuple[float, float, float]] = {}
        for metric_index, metric in enumerate(sorted(set(behavior_metrics + v2_metrics))):
            effect = scalar_difference_ci(left, right, metric, seed_base + pair_index * 100 + metric_index + 1)
            metric_values[metric] = effect
        for metric in behavior_metrics:
            effect, low, high = metric_values[metric]
            threshold = ACTION_THRESHOLD if metric == "semantic_actions" else RATE_THRESHOLD
            if abs(effect) >= threshold and not (low <= 0 <= high):
                behavior_pass.append(metric)
        for metric in v2_metrics:
            effect, low, high = metric_values[metric]
            threshold = ACTION_THRESHOLD if metric in {"semantic_actions", "v2_probe_count", "v2_probe_cost"} else RATE_THRESHOLD
            if abs(effect) >= threshold and not (low <= 0 <= high):
                v2_pass.append(metric)
        tv, pvalue = terminal_permutation(left, right, seed_base + pair_index * 100 + 90)
        if tv >= RATE_THRESHOLD and pvalue < 0.05:
            behavior_pass.append("terminal_distribution")
            v2_pass.append("terminal_distribution")
        output.append({
            "scope": scope, "reading_mode": mode, "left_method": left_name, "right_method": right_name,
            "instances_per_method": len(left), "SR_difference": sr_diff,
            "SR_ci_low": sr_low, "SR_ci_high": sr_high,
            "SR_indistinguishable": int(sr_indistinguishable),
            "behavior_separated": int(sr_indistinguishable and bool(behavior_pass)),
            "behavior_separating_metrics": ";".join(behavior_pass),
            "v2_separated": int(sr_indistinguishable and bool(v2_pass)),
            "v2_separating_metrics": ";".join(v2_pass),
            "terminal_TV": tv, "terminal_permutation_p": pvalue,
            **{
                f"{metric}_{suffix}": value
                for metric, values in metric_values.items()
                for suffix, value in zip(("difference", "ci_low", "ci_high"), values)
            },
        })
    return output


def behavior_distinguishability(
    pipeline_rows: list[dict[str, Any]], external_rows: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    output = []
    for mode in ("all_public", "llm_only", "oracle_reading"):
        scoped = [row for row in pipeline_rows if row["reading_mode"] == mode]
        methods = {method: rows for method, rows in group(scoped, "method").items()}
        base = methods["naive_rag"]
        methods["always_abstain"] = always_abstain_rows(base, "pipeline", mode)
        if set(methods) != set(PIPELINE_METHODS) | {"always_abstain"}:
            raise RuntimeError(f"scope A method set changed: {mode}/{sorted(methods)}")
        output.extend(pair_audit("public_11", mode, methods, 1000 + len(output)))

    first_external = {method: rows for method, rows in group(external_rows, "method").items()}
    external_keys = {
        (row["condition"], row["world"], row["task_id"])
        for row in first_external["direct_llm"]
    }
    selected_pipeline = [
        row for row in pipeline_rows
        if row["reading_mode"] == "all_public"
        and row["method"] in {"naive_rag", "verify_all", "arbiter", "oracle"}
        and (row["condition"], row["world"], row["task_id"]) in external_keys
    ]
    methods_b = dict(first_external)
    methods_b.update({method: rows for method, rows in group(selected_pipeline, "method").items()})
    methods_b["always_abstain"] = always_abstain_rows(methods_b["naive_rag"], "unified_110", "all_public")
    expected_b = set(EXTERNAL_PATHS) | {"naive_rag", "verify_all", "arbiter", "oracle", "always_abstain"}
    if set(methods_b) != expected_b or any(len(rows) != 110 for rows in methods_b.values()):
        raise RuntimeError("scope B method set or matrix changed")
    output.extend(pair_audit("unified_110", "all_public", methods_b, 50_000))

    summary = []
    for (scope, mode), rows in sorted(group(output, "scope", "reading_mode").items()):
        indistinguishable = [row for row in rows if int(row["SR_indistinguishable"])]
        behavior = [row for row in indistinguishable if int(row["behavior_separated"])]
        v2 = [row for row in indistinguishable if int(row["v2_separated"])]
        summary.append({
            "scope": scope, "reading_mode": mode, "method_pairs": len(rows),
            "SR_indistinguishable_pairs": len(indistinguishable),
            "behavior_separated_pairs": len(behavior),
            "behavior_unique_pairs": ";".join(f"{row['left_method']} vs {row['right_method']}" for row in behavior),
            "v2_separated_pairs": len(v2),
            "v2_unique_pairs": ";".join(f"{row['left_method']} vs {row['right_method']}" for row in v2),
        })
    return output, summary


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def report_text(
    identity: list[dict[str, Any]], probe_rows: list[dict[str, Any]],
    dependencies: list[dict[str, Any]], propagation: list[dict[str, Any]],
    condition_rows: list[dict[str, Any]], behavior: list[dict[str, Any]], qwen_status: str,
) -> str:
    naive_verify = [
        row for row in identity
        if row["left_method"] == "naive_rag" and row["right_method"] == "verify_all"
    ]
    label_behavior = any(int(row["identical_instances"]) != int(row["instances"]) for row in naive_verify)
    lines = [
        "# Measurement-v3 follow-up A4", "",
        "Zero LLM calls and zero new episodes were used. Frozen methods and prior results were not changed.", "",
        "## Action identity", "",
    ]
    for row in identity:
        lines.append(
            f"- {row['reading_mode']} / {row['left_method']} vs {row['right_method']}: "
            f"{row['identical_instances']}/{row['instances']} ({float(row['identity_rate']):.6f})."
        )
    lines.extend([
        "",
        "Label differences correspond to behavior differences."
        if label_behavior else "Label differences do not correspond to any semantic-action behavior difference.",
        f"Verify-All probe-position audit: {sum(int(row['naive_same_position_and_params']) for row in probe_rows)}/"
        f"{len(probe_rows)} matched Naive at the same semantic position and parameters.", "",
        "## Dependence after negative actions", "",
    ])
    for row in dependencies:
        lines.append(
            f"- {row['method']}: name reference {row['name_reference_events']}/{row['unique_negative_events']}; "
            f"FIFO causal-lot dependency {row['causal_lot_dependency_events']}/{row['unique_negative_events']}; "
            f"same-name other source {row['same_name_other_source_events']}."
        )
    lines.extend(["", "## External conditional propagation", ""])
    condition_index = {
        (row["method"], row["metric"]): row for row in condition_rows
    }
    for row in propagation:
        split = condition_index[(row["method"], "conditional")]
        lines.append(
            f"- {row['method']}: {row['propagation_events']}/{row['world_denied_stale_actions']} = "
            f"{row['conditional_propagation_rate']}; mismatched-matched={split['mismatched_minus_matched']} "
            f"[{split['difference_ci_low']}, {split['difference_ci_high']}]; "
            f"legal-format rate={row['legal_format_propagation_rate']}."
        )
    lines.extend(["", f"Qwen status: {qwen_status}.", "", "## Behavior-only distinguishability", ""])
    for row in behavior:
        lines.append(
            f"- {row['scope']} / {row['reading_mode']}: {row['SR_indistinguishable_pairs']} "
            f"SR-indistinguishable pairs; {row['behavior_separated_pairs']} behavior-separated; "
            f"v2={row['v2_separated_pairs']}."
        )
    lines.append("")
    return "\n".join(lines)


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError("A4 preregistration missing")
    if str(WEBCRAFT) not in sys.path:
        sys.path.insert(0, str(WEBCRAFT))
    conflicts = {row["id"]: row for row in load_conflicts(WEBCRAFT / "data/conflicts.jsonl")}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    pipeline_raw = pipeline_datasets()
    external_raw = external_sets()
    records, seed_notes = dedupe_pipeline_records(pipeline_raw)

    identity_summary, identity_detail, probes = action_identity(records)
    dependency_detail, dependency_summary = negative_dependencies(records, conflicts, nodes)
    propagation, propagation_conditions, propagation_events, propagation_witnesses, qwen_status = external_propagation(external_raw)
    pipeline_metrics = pipeline_behavior_rows(records)
    external_metrics = external_behavior_rows(external_raw)
    distinguish, distinguish_summary = behavior_distinguishability(pipeline_metrics, external_metrics)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "action_identity.csv", identity_summary)
    write_csv(OUT / "action_identity_mismatches.csv", identity_detail)
    write_csv(OUT / "verify_all_probe_position.csv", probes or [{
        "reading_mode": "none", "condition": "", "world": "", "task_id": "",
        "semantic_index": "", "method": "", "params": "", "naive_same_position_and_params": "",
    }])
    write_csv(OUT / "negative_dependency.csv", dependency_detail)
    write_csv(OUT / "negative_dependency_summary.csv", dependency_summary)
    write_csv(OUT / "external_propagation.csv", propagation)
    write_csv(OUT / "external_propagation_conditions.csv", propagation_conditions)
    write_csv(OUT / "external_propagation_events.csv", propagation_events)
    write_csv(OUT / "external_propagation_witnesses.csv", propagation_witnesses or [{
        "rule": "none", "agent_method": "", "condition": "", "world": "", "task_id": "",
        "stale_event_index": "", "stale_method": "", "stale_action": "",
        "predicted_products": "", "world_feedback": "",
        "later_event_index": "", "later_method": "", "later_action": "", "later_legal_format": "",
    }])
    write_csv(OUT / "behavior_distinguishability.csv", distinguish)
    write_csv(OUT / "behavior_distinguishability_summary.csv", distinguish_summary)
    (OUT / "REPORT.md").write_text(report_text(
        identity_summary, probes, dependency_summary, propagation,
        propagation_conditions, distinguish_summary, qwen_status,
    ))
    input_paths = [
        M3 / "pipeline_episodes_v3.csv", M3 / "external_episodes_v3.csv",
        M3 / "external_actions_v3.csv", M3 / "verify_all_unique.csv",
        ROOT / "artifacts/main/all_public/episodes.jsonl",
        ROOT / "artifacts/main/llm_only/episodes.jsonl",
        ROOT / "artifacts/post_review/oracle_reading/episodes.jsonl",
        ROOT / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl",
        ROOT / "artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl",
        *EXTERNAL_PATHS.values(),
    ]
    provenance = {
        "status": "MEASUREMENT_V3_FOLLOWUP_COMPLETE",
        "preregistration_commit": "f4bcd6e",
        "preregistration_sha256": sha256(PREREG),
        "llm_calls": 0, "new_episodes": 0,
        "bootstrap_draws": DRAWS, "bootstrap_seed": RNG_SEED,
        "rate_effect_threshold": RATE_THRESHOLD, "action_effect_threshold": ACTION_THRESHOLD,
        "pipeline_seed_checks": seed_notes, "qwen_status": qwen_status,
        "input_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in input_paths},
        "webcraft_conflicts_sha256": sha256(WEBCRAFT / "data/conflicts.jsonl"),
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": provenance["status"], "identity_rows": len(identity_detail),
        "negative_events": len(dependency_detail), "pairwise_rows": len(distinguish),
        "output": str(OUT),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
