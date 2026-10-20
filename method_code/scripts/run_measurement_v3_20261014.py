"""Run the preregistered 2026-10-14 measurement-v3 offline replay."""
from __future__ import annotations

import csv
import hashlib
import json
import math
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from arbiter.evaluation.consequence_htr import action_signature, consequence_actions
from arbiter.evaluation.external_scoring import ACTION_METHODS, _matches, load_conflicts, load_resource_nodes
from arbiter.evaluation.measurement_v3 import (
    actual_products, classify_actions_v3, episode_bounds, event_inputs, family,
    intrinsic_discriminative, item, predicted_products, prerequisite_ready, subject,
)


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
OUT = ROOT / "reports/main/post_review/measurement_v3"
PREREG = ROOT / "prereg/measurement_v3_20261014.md"
BOOTSTRAP_SEED = 20261014
BOOTSTRAP_DRAWS = 10_000
PIPELINE_METHODS_110 = (
    "naive_rag", "majority_rag", "truthfinder_rag", "verify_all", "arbiter", "oracle",
)
EXTERNAL_PATHS = {
    "direct_llm": ROOT / "artifacts/post_review/external_agents/final/direct_llm_assembled/episodes.jsonl",
    "react": ROOT / "artifacts/post_review/external_agents/final/react_assembled/episodes.jsonl",
    "reflexion": ROOT / "artifacts/post_review/external_agents/final/reflexion_assembled/episodes.jsonl",
    "adam": ROOT / "artifacts/post_review/external_agents/final/adam/episodes.jsonl",
    "xenon": ROOT / "artifacts/post_review/external_agents/final/xenon_assembled/episodes.jsonl",
}


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def csv_rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"refusing empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise RuntimeError(f"inconsistent columns: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def mean(values: Iterable[float]) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def group(rows: Iterable[dict[str, Any]], *fields: str) -> dict[Any, list[dict[str, Any]]]:
    output: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key = tuple(row[field] for field in fields)
        output[key[0] if len(key) == 1 else key].append(row)
    return output


def pipeline_datasets() -> dict[str, list[dict[str, Any]]]:
    return {
        "all_public": jsonl(ROOT / "artifacts/main/all_public/episodes.jsonl")
        + jsonl(ROOT / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl"),
        "llm_only": jsonl(ROOT / "artifacts/main/llm_only/episodes.jsonl")
        + jsonl(ROOT / "artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl"),
        "oracle_reading": jsonl(ROOT / "artifacts/post_review/oracle_reading/episodes.jsonl"),
    }


def external_sets() -> dict[str, list[dict[str, Any]]]:
    first = [row for path in EXTERNAL_PATHS.values() for row in jsonl(path)]
    oracle = jsonl(ROOT / "artifacts/post_review/external_controls/oracle_knowledge/episodes.jsonl")
    control = jsonl(ROOT / "artifacts/post_review/external_controls/control_all_public/episodes.jsonl")
    conditions = jsonl(ROOT / "artifacts/post_review/external_controls/conditions/episodes.jsonl")
    rerun = jsonl(ROOT / "artifacts/post_review/external_second_model_probe/episodes.jsonl")
    if Counter(row["method"] for row in first) != Counter({name: 110 for name in EXTERNAL_PATHS}):
        raise RuntimeError("first-run external matrix changed")
    if Counter(row["method"] for row in oracle) != Counter({"direct_llm": 110, "react": 110}):
        raise RuntimeError("oracle-knowledge matrix changed")
    if Counter(row["method"] for row in control) != Counter({"direct_llm": 60, "react": 60}):
        raise RuntimeError("control matrix changed")
    if Counter((row["method"], row["condition"]) for row in conditions) != Counter({
        (method, condition): 66 for method in ("direct_llm", "react")
        for condition in ("mixed", "polluted")
    }):
        raise RuntimeError("mixed/polluted matrix changed")
    if Counter(row["method"] for row in rerun) != Counter({"direct_llm": 110, "xenon": 40}):
        raise RuntimeError("same-model rerun matrix changed")
    return {
        "first_run": first,
        "oracle_knowledge": oracle,
        "control": control,
        "mixed_polluted": conditions,
        "same_model_rerun": rerun,
    }


def stale(vector: dict[str, Any], world: str) -> bool:
    return vector.get(world) is False and any(value is True for value in vector.values())


def task_index() -> dict[str, dict[str, Any]]:
    return {row["id"]: row for row in jsonl(WEBCRAFT / "data/tasks.jsonl")}


def pipeline_stale_map(record: dict[str, Any], conflicts: dict[str, dict[str, Any]]) -> dict[int, list[str]]:
    semantic = [i for i, event in enumerate(record["events"]) if event.get("method") in ACTION_METHODS]
    mechanisms = list(record.get("used_mechanism_ids", []))
    if len(semantic) != len(mechanisms):
        raise RuntimeError("pipeline action/mechanism alignment changed")
    return {
        index: [mechanism] for index, mechanism in zip(semantic, mechanisms)
        if mechanism in conflicts and stale(conflicts[mechanism]["truth_vector"], record["world"])
    }


def external_stale_map(
    events: list[dict[str, Any]], world: str, conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
) -> dict[int, list[str]]:
    return {
        index: sorted(row_id for row_id, row in conflicts.items()
                      if stale(row["truth_vector"], world) and _matches(event, row["assertion"], nodes))
        for index, event in enumerate(events)
        if event.get("method") in ACTION_METHODS
    }


def assertion_signature(assertion: dict[str, Any]) -> str:
    return json.dumps({
        "family": family(assertion), "subject": subject(assertion),
        "probe": assertion,
    }, sort_keys=True, separators=(",", ":"))


def annotate_discriminative(
    rows: list[dict[str, Any]], events: list[dict[str, Any]], world: str,
    conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
) -> None:
    for row in rows:
        event = events[int(row["event_index"])]
        intrinsic = intrinsic_discriminative(
            event, row["stale_mechanism_ids"], world=world,
            conflicts=conflicts, resource_nodes=nodes,
        )
        downstream = bool(row["referenced_at"])
        row["intrinsic_discriminative"] = int(intrinsic)
        row["discriminative"] = int(intrinsic or downstream)
        row["prerequisite_ready"] = prerequisite_ready(events, int(row["event_index"]))


def terminal_label(record: dict[str, Any], events: list[dict[str, Any]]) -> str:
    metrics = record.get("metrics") or {}
    if metrics.get("SR") or record.get("success") is True:
        return "success"
    if record.get("reported_feasible") is False:
        return "reported_infeasible"
    semantic = [event for event in events if event.get("method") in ACTION_METHODS]
    if semantic and semantic[-1].get("success") is False:
        return "execution_failed"
    if len(events) >= 90 or record.get("terminal") in {"action_budget", "call_budget", "budget_exhausted"}:
        return "budget_exhausted"
    return "plan_not_found"


def legacy_indices() -> tuple[dict[tuple[Any, ...], dict[str, str]], dict[tuple[Any, ...], dict[str, str]]]:
    pipeline = {
        (row["reading_mode"], row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"])): row
        for row in csv_rows(ROOT / "reports/main/post_review/htr_consequence/pipeline_episodes.csv")
    }
    external = {
        (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"])): row
        for row in csv_rows(ROOT / "reports/main/post_review/htr_consequence/external_episodes.csv")
    }
    return pipeline, external


def serialize_action(row: dict[str, Any]) -> dict[str, Any]:
    return {
        key: json.dumps(value, sort_keys=True, separators=(",", ":"))
        if isinstance(value, (list, dict)) else value
        for key, value in row.items()
    }


def score_pipeline(
    datasets: dict[str, list[dict[str, Any]]], conflicts: dict[str, dict[str, Any]],
    nodes: dict[str, str], tasks: dict[str, dict[str, Any]],
    legacy: dict[tuple[Any, ...], dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    action_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    equality_notes: list[str] = []
    accepted_roles = {
        (row["reading_mode"], row["method"], row["condition"], row["world"],
         row["task_id"], int(row["seed"]), int(row["event_index"])): row["plan_source_role"]
        for row in csv_rows(ROOT / "reports/main/post_review/htr_consequence/pipeline_actions.csv")
    }
    for mode, records in datasets.items():
        for record in records:
            stale_map = pipeline_stale_map(record, conflicts)
            actions = classify_actions_v3(
                record["events"], stale_by_event=stale_map, conflicts=conflicts,
                resource_nodes=nodes, target=tasks[record["task_id"]]["target"],
                episode_success=bool(record["metrics"]["SR"]),
            )
            annotate_discriminative(actions, record["events"], record["world"], conflicts, nodes)
            lower, upper = episode_bounds(actions)
            key = (mode, record["method"], record["condition"], record["world"],
                   record["task_id"], int(record["seed"]))
            old = legacy[key]
            base = {
                "setting": "pipeline_full", "reading_mode": mode, "method": record["method"],
                "condition": record["condition"], "world": record["world"],
                "task_id": record["task_id"], "seed": int(record["seed"]),
            }
            plan = int(record["metrics"]["HTR_commit"])
            episode_rows.append({
                **base,
                "HTR_commit_c_v3_lower": lower,
                "HTR_commit_c_v3_upper": upper,
                "HTR_commit_c_v2_rule_iii_includes_infeasible": int(old["HTR_commit_c"]),
                "HTR_commit_plan_source": plan,
                "HTR_all": int(record["metrics"]["HTR_all"]),
                "HTR_all_discriminative": int(any(row["discriminative"] for row in actions)),
                "stale_actions": len(actions),
                "discriminative_stale_actions": sum(row["discriminative"] for row in actions),
                "semantic_actions": sum(event.get("method") in ACTION_METHODS for event in record["events"]),
                "terminal": terminal_label(record, record["events"]),
            })
            for action in actions:
                event_index = int(action["event_index"])
                role_key = (mode, record["method"], record["condition"], record["world"],
                            record["task_id"], int(record["seed"]), event_index)
                role = accepted_roles[role_key]
                action_payload = {**action, "action_method": action["method"]}
                del action_payload["method"]
                action_rows.append(serialize_action({
                    **base, **action_payload,
                    "plan_source_role": "probe" if role == "probe" else "commit",
                    "plan_source_commit": int(role != "probe"),
                }))

    for (mode, method), rows in sorted(group(episode_rows, "reading_mode", "method").items()):
        by_key = group(rows, "condition", "world", "task_id")
        duplicate_groups = [values for values in by_key.values() if len(values) > 1]
        for values in duplicate_groups:
            compare = [
                tuple(row[name] for name in (
                    "HTR_commit_c_v3_lower", "HTR_commit_c_v3_upper",
                    "HTR_commit_c_v2_rule_iii_includes_infeasible", "HTR_commit_plan_source",
                    "HTR_all", "HTR_all_discriminative", "stale_actions",
                    "discriminative_stale_actions", "semantic_actions", "terminal",
                )) for row in values
            ]
            if any(value != compare[0] for value in compare[1:]):
                raise RuntimeError(f"pipeline seed mismatch: {mode}/{method}")
        equality_notes.append(f"{mode}/{method}: {len(duplicate_groups)} duplicate keys exactly equal")
    return action_rows, episode_rows, equality_notes


def score_external(
    datasets: dict[str, list[dict[str, Any]]], conflicts: dict[str, dict[str, Any]],
    nodes: dict[str, str], tasks: dict[str, dict[str, Any]],
    legacy: dict[tuple[Any, ...], dict[str, str]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], dict[str, str]]:
    action_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    hashes: dict[str, str] = {}
    for setting, records in datasets.items():
        for record in records:
            path = ROOT / record["action_log"]
            events = jsonl(path)
            hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            stale_map = external_stale_map(events, record["world"], conflicts, nodes)
            success = bool((record.get("metrics") or {}).get("SR", record.get("success", False)))
            actions = classify_actions_v3(
                events, stale_by_event=stale_map, conflicts=conflicts,
                resource_nodes=nodes, target=tasks[record["task_id"]]["target"],
                episode_success=success,
            )
            annotate_discriminative(actions, events, record["world"], conflicts, nodes)
            lower, upper = episode_bounds(actions)
            key = (record["method"], record["condition"], record["world"],
                   record["task_id"], int(record["seed"]))
            old = legacy.get(key) if setting == "first_run" else None
            metrics = record.get("metrics") or {}
            base = {
                "setting": setting, "reading_mode": "all_public",
                "method": record["method"], "condition": record["condition"],
                "world": record["world"], "task_id": record["task_id"],
                "seed": int(record["seed"]),
            }
            episode_rows.append({
                **base,
                "HTR_commit_c_v3_lower": lower,
                "HTR_commit_c_v3_upper": upper,
                "HTR_commit_c_v2_rule_iii_includes_infeasible": "" if old is None else int(old["HTR_commit_c"]),
                "legacy_self_reported_HTR_commit": int(metrics.get("self_reported_HTR_commit", metrics.get("HTR_commit", 0))),
                "HTR_all": int(bool(actions)),
                "HTR_all_discriminative": int(any(row["discriminative"] for row in actions)),
                "stale_actions": len(actions),
                "discriminative_stale_actions": sum(row["discriminative"] for row in actions),
                "semantic_actions": sum(event.get("method") in ACTION_METHODS for event in events),
                "terminal": terminal_label(record, events),
            })
            for action in actions:
                event = events[int(action["event_index"])]
                action_payload = {**action, "action_method": action["method"]}
                del action_payload["method"]
                action_rows.append(serialize_action({
                    **base, **action_payload, "self_reported_role": event.get("intent", "commit"),
                }))
    return action_rows, episode_rows, hashes


def dedupe_pipeline(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for _, values in sorted(group(rows, "reading_mode", "method", "condition", "world", "task_id").items()):
        selected = next((row for row in values if int(row["seed"]) == 20261006), min(values, key=lambda row: int(row["seed"])))
        output.append(selected)
    return output


def bootstrap_effect(
    method_rows: list[dict[str, Any]], naive_rows: list[dict[str, Any]], metric: str,
) -> dict[str, Any]:
    key_fields = ("condition", "world", "task_id")
    left = {tuple(row[name] for name in key_fields): float(row[metric]) for row in method_rows}
    right = {tuple(row[name] for name in key_fields): float(row[metric]) for row in naive_rows}
    if set(left) != set(right) or len(left) != 110:
        raise RuntimeError("unified 110 pairing failed")
    tasks = sorted({key[2] for key in left})
    if len(tasks) != 22:
        raise RuntimeError("unified comparison does not contain 22 tasks")
    left_task = np.array([sum(left[k] for k in left if k[2] == task) for task in tasks], dtype=float)
    right_task = np.array([sum(right[k] for k in right if k[2] == task) for task in tasks], dtype=float)
    rng = np.random.default_rng(BOOTSTRAP_SEED)
    diffs: list[float] = []
    ratios: list[float] = []
    omitted = 0
    for start in range(0, BOOTSTRAP_DRAWS, 500):
        count = min(500, BOOTSTRAP_DRAWS - start)
        indices = rng.integers(0, len(tasks), size=(count, len(tasks)))
        lvals = left_task[indices].sum(axis=1) / (len(tasks) * 5)
        rvals = right_task[indices].sum(axis=1) / (len(tasks) * 5)
        diffs.extend(((lvals - rvals) * 100).tolist())
        valid = rvals != 0
        ratios.extend((lvals[valid] / rvals[valid]).tolist())
        omitted += int((~valid).sum())
    point_left, point_right = mean(left.values()), mean(right.values())
    return {
        "difference_pp": (point_left - point_right) * 100,
        "difference_ci_low_pp": float(np.quantile(diffs, 0.025)),
        "difference_ci_high_pp": float(np.quantile(diffs, 0.975)),
        "ratio": point_left / point_right if point_right else "",
        "ratio_ci_low": float(np.quantile(ratios, 0.025)) if ratios else "",
        "ratio_ci_high": float(np.quantile(ratios, 0.975)) if ratios else "",
        "ratio_draws_omitted": omitted,
    }


def unified_110(
    pipeline: list[dict[str, Any]], external: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    external_first = [row for row in external if row["setting"] == "first_run"]
    keys = {
        (row["condition"], row["world"], row["task_id"])
        for row in external_first if row["method"] == "direct_llm"
    }
    if len(keys) != 110:
        raise RuntimeError("external common matrix does not have 110 keys")
    pipeline_deduped = dedupe_pipeline([
        row for row in pipeline
        if row["reading_mode"] == "all_public" and row["method"] in PIPELINE_METHODS_110
        and (row["condition"], row["world"], row["task_id"]) in keys
    ])
    combined: dict[str, list[dict[str, Any]]] = {
        method: [row for row in pipeline_deduped if row["method"] == method]
        for method in PIPELINE_METHODS_110
    }
    combined.update({
        method: [row for row in external_first if row["method"] == method]
        for method in EXTERNAL_PATHS
    })
    combined["always_abstain"] = [{
        "condition": condition, "world": world, "task_id": task,
        "HTR_commit_c_v3_lower": 0, "HTR_commit_c_v3_upper": 0,
        "HTR_commit_c_v2_rule_iii_includes_infeasible": 0,
        "HTR_commit_plan_source": "", "HTR_all": 0, "HTR_all_discriminative": 0,
    } for condition, world, task in sorted(keys)]
    for method, rows in combined.items():
        if len(rows) != 110:
            raise RuntimeError(f"unified matrix incomplete for {method}: {len(rows)}")
    metrics = (
        "HTR_commit_c_v3_lower", "HTR_commit_c_v3_upper",
        "HTR_commit_c_v2_rule_iii_includes_infeasible", "HTR_all", "HTR_all_discriminative",
    )
    summaries = []
    effects = []
    naive = combined["naive_rag"]
    for method, rows in combined.items():
        summary = {
            "method": method, "instances": len(rows),
            **{metric: mean(float(row[metric]) for row in rows if row[metric] != "") for metric in metrics},
            "HTR_commit_plan_source": mean(
                float(row["HTR_commit_plan_source"]) for row in rows
                if row.get("HTR_commit_plan_source", "") != ""
            ) if any(row.get("HTR_commit_plan_source", "") != "" for row in rows) else "",
        }
        summaries.append(summary)
        if method in EXTERNAL_PATHS:
            for metric in ("HTR_commit_c_v3_lower", "HTR_commit_c_v3_upper", "HTR_all", "HTR_all_discriminative"):
                effects.append({
                    "method": method, "reference": "naive_rag", "metric": metric,
                    **bootstrap_effect(rows, naive, metric),
                    "bootstrap_units": 22, "cells_per_task": 5,
                    "bootstrap_draws": BOOTSTRAP_DRAWS, "bootstrap_seed": BOOTSTRAP_SEED,
                })
    return summaries, effects


def pipeline_full(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped = dedupe_pipeline(rows)
    output = []
    for (mode, method), values in sorted(group(deduped, "reading_mode", "method").items()):
        if len(values) != 374:
            raise RuntimeError(f"pipeline full matrix changed: {mode}/{method}={len(values)}")
        output.append({
            "reading_mode": mode, "method": method, "instances": len(values),
            "HTR_commit_c_v3_lower": mean(row["HTR_commit_c_v3_lower"] for row in values),
            "HTR_commit_c_v3_upper": mean(row["HTR_commit_c_v3_upper"] for row in values),
            "HTR_commit_c_v2_rule_iii_includes_infeasible": mean(
                row["HTR_commit_c_v2_rule_iii_includes_infeasible"] for row in values),
            "HTR_commit_plan_source": mean(row["HTR_commit_plan_source"] for row in values),
            "HTR_all": mean(row["HTR_all"] for row in values),
            "HTR_all_discriminative": mean(row["HTR_all_discriminative"] for row in values),
            "stale_actions": sum(row["stale_actions"] for row in values),
            "discriminative_stale_actions": sum(row["discriminative_stale_actions"] for row in values),
        })
    return output


def precision_recall(rows: list[dict[str, Any]], permissive: bool) -> tuple[Any, Any]:
    predicted = [
        row["v3_label"] == "commit" or (permissive and row["v3_label"] == "undetermined")
        for row in rows
    ]
    actual = [bool(int(row["plan_source_commit"])) for row in rows]
    tp = sum(p and a for p, a in zip(predicted, actual))
    fp = sum(p and not a for p, a in zip(predicted, actual))
    fn = sum(not p and a for p, a in zip(predicted, actual))
    return (tp / (tp + fp) if tp + fp else "", tp / (tp + fn) if tp + fn else "")


def confusion_outputs(
    actions: list[dict[str, Any]], episodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], Counter[str]]:
    action_rows = [{**row, "plan_source_commit": int(row["plan_source_commit"])} for row in actions]
    episode_pairs = [{
        "reading_mode": row["reading_mode"], "method": row["method"],
        "v3_label": (
            "commit" if row["HTR_commit_c_v3_lower"] else
            "undetermined" if row["HTR_commit_c_v3_upper"] else "verify"
        ),
        "plan_source_commit": row["HTR_commit_plan_source"],
    } for row in episodes]
    output = []
    summaries = []
    for level, source in (("action", action_rows), ("episode", episode_pairs)):
        grouped = group(source, "reading_mode", "method")
        for mode, method in sorted({(row["reading_mode"], row["method"]) for row in episodes}):
            grouped.setdefault((mode, method), [])
        for mode in sorted({row["reading_mode"] for row in source}):
            grouped[(mode, "__all_methods__")] = [row for row in source if row["reading_mode"] == mode]
        for (mode, method), rows in sorted(grouped.items()):
            counts = Counter((row["v3_label"], int(row["plan_source_commit"])) for row in rows)
            for label in ("commit", "verify", "undetermined"):
                for plan in (0, 1):
                    output.append({
                        "level": level, "reading_mode": mode, "method": method,
                        "v3_label": label, "plan_source_commit": plan,
                        "count": counts[(label, plan)],
                    })
            strict_p, strict_r = precision_recall(rows, False)
            permissive_p, permissive_r = precision_recall(rows, True)
            summaries.append({
                "level": level, "reading_mode": mode, "method": method,
                "observations": len(rows),
                "strict_precision": strict_p, "strict_recall": strict_r,
                "permissive_precision": permissive_p, "permissive_recall": permissive_r,
                "identifiable_coverage": (
                    mean(row["v3_label"] != "undetermined" for row in rows) if rows else ""
                ),
            })
    old = [
        row for row in csv_rows(ROOT / "reports/main/post_review/htr_consequence/disagreements.csv")
        if row["reason"] == "terminal_submission_after_probe"
    ]
    destinations: Counter[str] = Counter()
    episode_v3 = {
        (row["reading_mode"], row["method"], row["condition"], row["world"], row["task_id"], str(row["seed"])):
        ("commit" if row["HTR_commit_c_v3_lower"] else
         "undetermined" if row["HTR_commit_c_v3_upper"] else "verify")
        for row in episodes
    }
    for row in old:
        key = tuple(row[name] for name in ("reading_mode", "method", "condition", "world", "task_id", "seed"))
        destinations[episode_v3[key]] += 1
    if sum(destinations.values()) != 1123:
        raise RuntimeError(f"v2 disagreement trace changed: {sum(destinations.values())}")
    return output, summaries, destinations


def discriminative_summary(
    pipeline_actions: list[dict[str, Any]], pipeline_episodes: list[dict[str, Any]],
    external_actions: list[dict[str, Any]], external_episodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    detail = []
    for agent_family, actions, episodes in (
        ("pipeline", pipeline_actions, pipeline_episodes),
        ("external", external_actions, external_episodes),
    ):
        episode_lookup = {
            tuple(row.get(name, "") for name in (
                "setting", "reading_mode", "method", "condition", "world", "task_id", "seed",
            )): row for row in episodes
        }
        for row in actions:
            key = tuple(row.get(name, "") for name in (
                "setting", "reading_mode", "method", "condition", "world", "task_id", "seed",
            ))
            episode = episode_lookup[key]
            detail.append({
                "agent_family": agent_family,
                "setting": row.get("setting", "pipeline_full"),
                "reading_mode": row["reading_mode"], "method": row["method"],
                "condition": row["condition"], "world": row["world"],
                "task_id": row["task_id"], "seed": row["seed"],
                "event_index": row["event_index"], "action_method": row["action_method"],
                "mechanism_families": row["mechanism_families"],
                "v3_label": row["v3_label"], "discriminative": int(row["discriminative"]),
                "intrinsic_discriminative": int(row["intrinsic_discriminative"]),
                "prerequisite_ready": row["prerequisite_ready"],
                "episode_HTR_all": episode["HTR_all"],
                "episode_HTR_all_discriminative": episode["HTR_all_discriminative"],
            })
    summaries = []
    all_episodes = [
        {**row, "agent_family": "pipeline", "setting": "pipeline_full"}
        for row in pipeline_episodes
    ] + [
        {**row, "agent_family": "external"} for row in external_episodes
    ]
    detail_groups = group(detail, "agent_family", "setting", "reading_mode", "method")
    for (agent, setting, mode, method), episodes in sorted(group(
        all_episodes, "agent_family", "setting", "reading_mode", "method",
    ).items()):
        rows = detail_groups.get((agent, setting, mode, method), [])
        families = Counter()
        for row in rows:
            for value in json.loads(row["mechanism_families"]):
                families[value] += 1
        summaries.append({
            "agent_family": agent, "setting": setting, "reading_mode": mode,
            "method": method, "episodes": len(episodes),
            "HTR_all": mean(row["HTR_all"] for row in episodes),
            "HTR_all_discriminative": mean(row["HTR_all_discriminative"] for row in episodes),
            "stale_actions": len(rows),
            "non_discriminative_actions": sum(not row["discriminative"] for row in rows),
            "non_discriminative_share": mean(not row["discriminative"] for row in rows) if rows else "",
            "drop_actions": families["drop"], "recipe_actions": families["recipe"],
            "smelting_actions": families["smelting"], "name_actions": families["name"],
            "other_actions": families["other"],
        })
    return detail, summaries


def mechanism_text_match(text: str, assertion: dict[str, Any]) -> bool:
    normalized = " ".join(text.lower().replace("minecraft:", "").replace("_", " ").split())
    value = subject(assertion).replace("_", " ").lower()
    if value not in normalized:
        return False
    marker = {
        "drop": (" drops ", "breaking "),
        "recipe": ("crafting recipe",),
        "smelting": ("smelting ", "furnace"),
        "name": ("exists", "resolvable", "identifier"),
        "other": tuple(),
    }[family(assertion)]
    return not marker or any(token in normalized for token in marker)


def oracle_evidence(
    external_actions: list[dict[str, Any]], external_records: list[dict[str, Any]],
    conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
) -> list[dict[str, Any]]:
    records = {
        (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"])): row
        for row in external_records
    }
    output = []
    for row in external_actions:
        if row["setting"] != "oracle_knowledge" or not int(row["discriminative"]):
            continue
        key = (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))
        record = records[key]
        events = jsonl(ROOT / record["action_log"])
        event_index = int(row["event_index"])
        stale_ids = json.loads(row["stale_mechanism_ids"])
        correct = []
        for stale_id in stale_ids:
            assertion = conflicts[stale_id]["assertion"]
            correct.extend(
                candidate["assertion"] for candidate in conflicts.values()
                if candidate["truth_vector"].get(row["world"]) is True
                and family(candidate["assertion"]) == family(assertion)
                and subject(candidate["assertion"]) == subject(assertion)
            )
        matches = []
        for search_index, event in enumerate(events[:event_index]):
            if event.get("method") != "search":
                continue
            for position, hit in enumerate(event.get("result", []), 1):
                if isinstance(hit, dict) and any(mechanism_text_match(str(hit.get("text", "")), assertion) for assertion in correct):
                    gap = sum(
                        candidate.get("method") in ACTION_METHODS
                        for candidate in events[search_index + 1:event_index]
                    )
                    matches.append((search_index, position, gap, str(hit.get("chunk_id", ""))))
        first = min(matches, default=None)
        output.append({
            "method": row["method"], "condition": row["condition"], "world": row["world"],
            "task_id": row["task_id"], "seed": row["seed"], "event_index": event_index,
            "stale_mechanism_ids": row["stale_mechanism_ids"],
            "mechanism_families": row["mechanism_families"], "v3_label": row["v3_label"],
            "correct_claim_in_prior_context": int(first is not None),
            "first_search_event_index": "" if first is None else first[0],
            "first_result_position": "" if first is None else first[1],
            "semantic_action_gap": "" if first is None else first[2],
            "chunk_id": "" if first is None else first[3],
            "prerequisite_ready": row["prerequisite_ready"],
        })
    if not output:
        return [{
            "method": "none", "condition": "", "world": "", "task_id": "", "seed": "",
            "event_index": "", "stale_mechanism_ids": "[]", "mechanism_families": "[]",
            "v3_label": "", "correct_claim_in_prior_context": 0,
            "first_search_event_index": "", "first_result_position": "",
            "semantic_action_gap": "", "chunk_id": "", "prerequisite_ready": "",
        }]
    return output


def opportunity_rows(episodes: list[dict[str, Any]], family_name: str) -> list[dict[str, Any]]:
    output = []
    for row in episodes:
        semantic = int(row["semantic_actions"])
        stale_count = int(row["stale_actions"])
        output.append({
            "agent_family": family_name, "setting": row.get("setting", "pipeline_full"),
            "reading_mode": row["reading_mode"], "method": row["method"],
            "condition": row["condition"], "world": row["world"], "task_id": row["task_id"],
            "seed": row["seed"], "semantic_actions": semantic, "stale_actions": stale_count,
            "stale_actions_per_10_semantic": stale_count * 10 / semantic if semantic else "",
            "actions_before_first_stale": "",
        })
    return output


def fill_first_stale(opportunities: list[dict[str, Any]], actions: list[dict[str, Any]]) -> None:
    first = {}
    for row in actions:
        key = tuple(row.get(name, "") for name in (
            "setting", "reading_mode", "method", "condition", "world", "task_id", "seed",
        ))
        first[key] = min(first.get(key, 10**9), int(row["semantic_index"]))
    for row in opportunities:
        key = tuple(row.get(name, "") for name in (
            "setting", "reading_mode", "method", "condition", "world", "task_id", "seed",
        ))
        row["actions_before_first_stale"] = first.get(key, "")


def verify_all_unique(
    pipeline_records: dict[str, list[dict[str, Any]]], nodes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[str]]:
    old = csv_rows(ROOT / "reports/main/post_review/htr_consequence/verify_all_after_negative.csv")
    records = {
        (mode, row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"])): row
        for mode, values in pipeline_records.items() for row in values
    }
    enriched = []
    for row in old:
        key = (row["reading_mode"], "verify_all", row["condition"], row["world"], row["task_id"], int(row["seed"]))
        record = records[key]
        event_index = int(row["event_index"])
        enriched.append({
            **row,
            "signature": action_signature(record["events"][event_index], nodes),
            "unique_key": json.dumps([
                row["reading_mode"], row["condition"], row["world"], row["task_id"],
                event_index, row["mechanism_id"], action_signature(record["events"][event_index], nodes),
            ], separators=(",", ":")),
        })
    duplicate_disagreements = []
    unique = []
    for unique_key, values in group(enriched, "unique_key").items():
        behaviors = {row["next_behavior"] for row in values}
        if len(behaviors) != 1:
            duplicate_disagreements.append(unique_key)
            continue
        unique.append(next((row for row in values if int(row["seed"]) == 20261006), min(values, key=lambda x: int(x["seed"]))))
    output = []
    for row in unique:
        mode = row["reading_mode"]
        naive_candidates = [
            record for key, record in records.items()
            if key[0] == mode and key[1] == "naive_rag" and key[2] == row["condition"]
            and key[3] == row["world"] and key[4] == row["task_id"] and key[5] == int(row["seed"])
        ]
        naive = naive_candidates[0] if naive_candidates else None
        same = [] if naive is None else [
            (index, event) for index, event in enumerate(naive["events"])
            if event.get("method") in ACTION_METHODS and action_signature(event, nodes) == row["signature"]
        ]
        predicted = set()
        if naive is not None:
            for index, _ in same:
                semantic = [i for i, event in enumerate(naive["events"]) if event.get("method") in ACTION_METHODS]
                pos = semantic.index(index)
                if pos < len(naive.get("used_mechanism_ids", [])):
                    trace = naive.get("policy_trace", [])[pos] if pos < len(naive.get("policy_trace", [])) else {}
                    try:
                        parsed = json.loads(trace.get("action_signature", "null"))
                    except json.JSONDecodeError:
                        parsed = None
                    if isinstance(parsed, list) and len(parsed) >= 3 and isinstance(parsed[2], str):
                        predicted.add(str(item(parsed[2])))
        consumed = bool(naive and any(
            event_inputs(event) & predicted for event in naive["events"]
            if event.get("method") in ACTION_METHODS
        ))
        output.append({
            "reading_mode": mode, "condition": row["condition"], "world": row["world"],
            "task_id": row["task_id"], "retained_seed": row["seed"],
            "event_index": row["event_index"], "mechanism_id": row["mechanism_id"],
            "next_behavior": row["next_behavior"], "source_record_count": len(group(enriched, "unique_key")[row["unique_key"]]),
            "naive_same_stale_action": int(bool(same)),
            "naive_consumed_predicted_product": int(consumed),
            "naive_terminal": "" if naive is None else terminal_label(naive, naive["events"]),
        })
    summary = []
    original_by_mode = group(enriched, "reading_mode")
    unique_by_mode = group(output, "reading_mode")
    for mode in sorted(set(original_by_mode) | set(unique_by_mode)):
        original_counts = Counter(row["next_behavior"] for row in original_by_mode.get(mode, []))
        unique_counts = Counter(row["next_behavior"] for row in unique_by_mode.get(mode, []))
        summary.append({
            "reading_mode": mode, "original_records": len(original_by_mode.get(mode, [])),
            "unique_events": len(unique_by_mode.get(mode, [])),
            "original_abstain": original_counts["abstain"], "unique_abstain": unique_counts["abstain"],
            "original_replan": original_counts["replan"], "unique_replan": unique_counts["replan"],
            "original_continue": original_counts["continue_original_plan"],
            "unique_continue": unique_counts["continue_original_plan"],
        })
    return output, summary, duplicate_disagreements


def opportunity_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for (agent, setting, mode, method), values in sorted(group(
        rows, "agent_family", "setting", "reading_mode", "method",
    ).items()):
        first = [int(row["actions_before_first_stale"]) for row in values if row["actions_before_first_stale"] != ""]
        output.append({
            "agent_family": agent, "setting": setting, "reading_mode": mode,
            "method": method, "episodes": len(values),
            "semantic_actions_mean": mean(int(row["semantic_actions"]) for row in values),
            "stale_actions_per_10_semantic_mean": mean(
                float(row["stale_actions_per_10_semantic"])
                for row in values if row["stale_actions_per_10_semantic"] != ""
            ),
            "episodes_with_stale_action": len(first),
            "actions_before_first_stale_mean": mean(first) if first else "",
            "actions_before_first_stale_median": float(np.median(first)) if first else "",
        })
    return output


def opportunity_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for method in ("direct_llm", "react"):
        first = {
            (row["condition"], row["world"], row["task_id"]): row
            for row in rows if row["agent_family"] == "external"
            and row["setting"] == "first_run" and row["method"] == method
        }
        oracle = {
            (row["condition"], row["world"], row["task_id"]): row
            for row in rows if row["agent_family"] == "external"
            and row["setting"] == "oracle_knowledge" and row["method"] == method
        }
        if set(first) != set(oracle) or len(first) != 110:
            raise RuntimeError(f"oracle opportunity pairing failed for {method}")
        for metric in ("semantic_actions", "stale_actions_per_10_semantic", "actions_before_first_stale"):
            pairs = [
                (float(oracle[key][metric]), float(first[key][metric]))
                for key in first if oracle[key][metric] != "" and first[key][metric] != ""
            ]
            output.append({
                "method": method, "metric": metric, "paired_instances": len(pairs),
                "first_run_mean": mean(right for _, right in pairs),
                "oracle_knowledge_mean": mean(left for left, _ in pairs),
                "oracle_minus_first": mean(left - right for left, right in pairs),
            })
    return output


def confusion_markdown(
    summaries: list[dict[str, Any]], destinations: Counter[str], pipeline_actions: list[dict[str, Any]],
) -> str:
    lines = [
        "# Measurement-v3 confusion summary", "",
        "The plan-source label is the saved reference. Strict scoring treats only v3 commit as positive; "
        "permissive scoring also treats undetermined as positive.", "",
        "| Reading mode | Level | Precision strict | Recall strict | Precision permissive | Recall permissive | Coverage |",
        "|---|---|---:|---:|---:|---:|---:|",
    ]
    for mode in sorted({row["reading_mode"] for row in pipeline_actions}):
        row = next(value for value in summaries if value["reading_mode"] == mode
                   and value["method"] == "__all_methods__" and value["level"] == "action")
        def display(value: Any) -> str:
            return "NA" if value == "" else f"{float(value):.6f}"
        lines.append(
            f"| {mode} | action | {display(row['strict_precision'])} | {display(row['strict_recall'])} | "
            f"{display(row['permissive_precision'])} | {display(row['permissive_recall'])} | "
            f"{float(row['identifiable_coverage']):.6f} |"
        )
    lines.extend([
        "", "## V2 disagreement destinations", "",
        "The 1,123 v2 `terminal_submission_after_probe` disagreements move as follows under v3:", "",
        *[f"- {label}: {destinations[label]}" for label in ("commit", "verify", "undetermined")], "",
    ])
    return "\n".join(lines)


def report_text(
    confusion_summaries: list[dict[str, Any]], effects: list[dict[str, Any]],
    disc_summary: list[dict[str, Any]], evidence: list[dict[str, Any]],
    verify_summary: list[dict[str, Any]], duplicate_disagreements: list[str],
) -> str:
    pipeline_action = [
        row for row in confusion_summaries
        if row["level"] == "action" and row["method"] == "__all_methods__"
    ]
    external_effects = [
        row for row in effects if row["metric"] in {
            "HTR_commit_c_v3_lower", "HTR_commit_c_v3_upper",
        }
    ]
    first = [row for row in disc_summary if row["agent_family"] == "external" and row["setting"] == "first_run"]
    oracle = [row for row in disc_summary if row["agent_family"] == "external" and row["setting"] == "oracle_knowledge"]
    evidence_events = [
        row for row in evidence
        if int(row["correct_claim_in_prior_context"]) and row["v3_label"] == "commit"
    ]
    lines = [
        "# Measurement reanalysis v3", "",
        "Zero model calls and zero new episodes were used. Existing reported values remain unchanged; v3 is parallel measurement.", "",
        "## Pipeline plan-source comparison", "",
    ]
    for mode in sorted({row["reading_mode"] for row in pipeline_action}):
        row = next(row for row in pipeline_action if row["reading_mode"] == mode)
        strict_precision = "NA" if row["strict_precision"] == "" else f"{float(row['strict_precision']):.6f}"
        strict_recall = "NA" if row["strict_recall"] == "" else f"{float(row['strict_recall']):.6f}"
        permissive_precision = "NA" if row["permissive_precision"] == "" else f"{float(row['permissive_precision']):.6f}"
        permissive_recall = "NA" if row["permissive_recall"] == "" else f"{float(row['permissive_recall']):.6f}"
        lines.append(
            f"- {mode}: stale actions={row['observations']}; micro strict precision={strict_precision}, "
            f"micro strict recall={strict_recall}, permissive precision={permissive_precision}, "
            f"permissive recall={permissive_recall}, identifiable coverage={float(row['identifiable_coverage']):.6f}."
        )
    lines.extend(["", "## Unified 110 external-minus-Naive effects", ""])
    for row in external_effects:
        lines.append(
            f"- {row['method']} / {row['metric']}: {row['difference_pp']:.6f} pp "
            f"[{row['difference_ci_low_pp']:.6f}, {row['difference_ci_high_pp']:.6f}]."
        )
    first_nondisc = sum(int(row["non_discriminative_actions"]) for row in first)
    first_stale = sum(int(row["stale_actions"]) for row in first)
    oracle_nondisc = sum(int(row["non_discriminative_actions"]) for row in oracle)
    oracle_stale = sum(int(row["stale_actions"]) for row in oracle)
    lines.extend([
        "", "## Discriminative stale actions", "",
        f"- External first run: {first_nondisc}/{first_stale} stale actions were non-discriminative "
        f"({first_nondisc / first_stale if first_stale else 0:.6f}).",
        f"- Oracle knowledge: {oracle_nondisc}/{oracle_stale} stale actions were non-discriminative "
        f"({oracle_nondisc / oracle_stale if oracle_stale else 0:.6f}).",
        f"- Correct target-world context + discriminative + v3 commit: {len(evidence_events)} events in "
        f"{len({(row['method'], row['condition'], row['world'], row['task_id']) for row in evidence_events})} episodes.",
    ])
    if not evidence_events:
        lines.append("- Required conclusion: withdraw the claim that a parametric prior overrode contextual evidence.")
    else:
        lines.append("- Required conclusion: report this as an observation only; no causal claim is made.")
    lines.extend(["", "## Verify-All unique events", ""])
    for row in verify_summary:
        lines.append(
            f"- {row['reading_mode']}: records={row['original_records']}, unique={row['unique_events']}, "
            f"continue={row['original_continue']} -> {row['unique_continue']}."
        )
    lines.append(f"- Duplicate groups with inconsistent behavior: {len(duplicate_disagreements)}.")
    continue_records = sum(int(row["original_continue"]) for row in verify_summary)
    continue_unique = sum(int(row["unique_continue"]) for row in verify_summary)
    lines.append(
        f"- Dependency result: 0/{continue_records} saved continue records, equivalent to 0/{continue_unique} unique events."
    )
    lines.append("")
    return "\n".join(lines)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    if not PREREG.exists():
        raise RuntimeError("measurement-v3 preregistration is missing")
    if str(WEBCRAFT) not in sys.path:
        sys.path.insert(0, str(WEBCRAFT))
    conflict_rows = load_conflicts(WEBCRAFT / "data/conflicts.jsonl")
    conflicts = {row["id"]: row for row in conflict_rows}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    tasks = task_index()
    pipeline_records = pipeline_datasets()
    external_records = external_sets()
    pipeline_legacy, external_legacy = legacy_indices()

    pipeline_actions, pipeline_episodes, seed_notes = score_pipeline(
        pipeline_records, conflicts, nodes, tasks, pipeline_legacy,
    )
    external_actions, external_episodes, action_hashes = score_external(
        external_records, conflicts, nodes, tasks, external_legacy,
    )
    unified, effects = unified_110(pipeline_episodes, external_episodes)
    full = pipeline_full(pipeline_episodes)
    confusion, confusion_summary, destinations = confusion_outputs(
        pipeline_actions, pipeline_episodes,
    )
    disc_detail, disc_summary = discriminative_summary(
        pipeline_actions, pipeline_episodes, external_actions, external_episodes,
    )
    evidence = oracle_evidence(external_actions, external_records["oracle_knowledge"], conflicts, nodes)
    opportunities = opportunity_rows(pipeline_episodes, "pipeline") + opportunity_rows(external_episodes, "external")
    fill_first_stale(opportunities, pipeline_actions + external_actions)
    opportunity_aggregates = opportunity_summary(opportunities)
    opportunity_comparisons = opportunity_pairs(opportunities)
    verify_detail, verify_summary, duplicate_disagreements = verify_all_unique(pipeline_records, nodes)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "pipeline_actions_v3.csv", pipeline_actions)
    write_csv(OUT / "pipeline_episodes_v3.csv", pipeline_episodes)
    write_csv(OUT / "external_actions_v3.csv", external_actions)
    write_csv(OUT / "external_episodes_v3.csv", external_episodes)
    write_csv(OUT / "unified_110.csv", unified)
    write_csv(OUT / "unified_110_effects.csv", effects)
    write_csv(OUT / "pipeline_full_v3.csv", full)
    write_csv(OUT / "confusion_v3.csv", confusion)
    write_csv(OUT / "confusion_metrics_v3.csv", confusion_summary)
    write_csv(OUT / "discriminative_actions.csv", disc_detail)
    write_csv(OUT / "discriminative_summary.csv", disc_summary)
    write_csv(OUT / "oracle_knowledge_evidence.csv", evidence)
    write_csv(OUT / "opportunity_normalized.csv", opportunities)
    write_csv(OUT / "opportunity_summary.csv", opportunity_aggregates)
    write_csv(OUT / "opportunity_oracle_vs_first.csv", opportunity_comparisons)
    write_csv(OUT / "verify_all_unique.csv", verify_detail)
    write_csv(OUT / "verify_all_unique_summary.csv", verify_summary)
    (OUT / "confusion_summary.md").write_text(
        confusion_markdown(confusion_summary, destinations, pipeline_actions)
    )
    (OUT / "verify_all_unique.md").write_text("\n".join([
        "# Verify-All seed-deduplicated events", "",
        "The historical table contains one row per saved seed. The unique key is reading mode, cell, task, "
        "event index, stale mechanism, and normalized action signature. Agreeing duplicates retain seed 20261006.", "",
        "The earlier 0/138 result means none of the 138 recorded continue events consumed the stale predicted "
        "product. This file reports both those 138 records and their unique-event count; it does not reinterpret them.", "",
        f"Duplicate groups with inconsistent behavior: {len(duplicate_disagreements)}.", "",
    ]))
    (OUT / "REPORT.md").write_text(report_text(
        confusion_summary, effects, disc_summary, evidence, verify_summary, duplicate_disagreements,
    ))
    input_paths = [
        ROOT / "artifacts/main/all_public/episodes.jsonl",
        ROOT / "artifacts/main/llm_only/episodes.jsonl",
        ROOT / "artifacts/post_review/oracle_reading/episodes.jsonl",
        ROOT / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl",
        ROOT / "artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl",
        ROOT / "artifacts/post_review/external_controls/oracle_knowledge/episodes.jsonl",
        ROOT / "artifacts/post_review/external_controls/control_all_public/episodes.jsonl",
        ROOT / "artifacts/post_review/external_controls/conditions/episodes.jsonl",
        ROOT / "artifacts/post_review/external_second_model_probe/episodes.jsonl",
        *EXTERNAL_PATHS.values(),
    ]
    provenance = {
        "status": "MEASUREMENT_V3_COMPLETE",
        "preregistration_commit": "7b31346",
        "preregistration_sha256": sha256(PREREG),
        "llm_calls": 0,
        "new_episodes": 0,
        "bootstrap_seed": BOOTSTRAP_SEED,
        "bootstrap_draws": BOOTSTRAP_DRAWS,
        "pipeline_episode_rows": len(pipeline_episodes),
        "external_episode_rows": len(external_episodes),
        "pipeline_seed_checks": seed_notes,
        "verify_duplicate_disagreements": duplicate_disagreements,
        "input_sha256": {str(path.relative_to(ROOT)): sha256(path) for path in input_paths},
        "external_action_log_sha256": action_hashes,
        "webcraft_conflicts_sha256": sha256(WEBCRAFT / "data/conflicts.jsonl"),
        "webcraft_tasks_sha256": sha256(WEBCRAFT / "data/tasks.jsonl"),
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": provenance["status"], "pipeline_episodes": len(pipeline_episodes),
        "external_episodes": len(external_episodes), "output": str(OUT),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
