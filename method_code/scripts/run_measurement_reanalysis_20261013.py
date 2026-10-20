"""Offline measurement reanalysis registered on 2026-10-13."""
from __future__ import annotations

import csv
import hashlib
import json
import random
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from arbiter.evaluation.consequence_htr import (
    action_signature, consequence_actions, episode_htr,
)
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes
from arbiter.evaluation.p6_execution import _claims, _compiler, _probe_then_rag_filter_bundle
from arbiter.evaluation.post_review_measurement import terminal
from arbiter.evaluation.reporting import EpisodeRecord, _components
from arbiter.pinning import build_pin


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
OUT = ROOT / "reports/main/post_review/htr_consequence"
POST_OUT = ROOT / "reports/main/post_review"
PIPELINE_ACTIONS = frozenset(ACTION_METHODS)
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
        raise ValueError(f"empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise ValueError(f"inconsistent fields: {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def mean(values: Iterable[float]) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def verify_frozen_pin_subset() -> tuple[str, str]:
    """Allow later additive preregistration files, never changed frozen bytes."""
    expected = json.loads((ROOT / "config/webcraft_pin.json").read_text())
    actual = build_pin(WEBCRAFT)
    for root_name, expected_root in expected["roots"].items():
        actual_root = actual["roots"].get(root_name, {"exists": False, "files": {}})
        if expected_root["exists"] != actual_root["exists"]:
            raise RuntimeError(f"frozen WebCraft root existence changed: {root_name}")
        for path, digest in expected_root["files"].items():
            if actual_root["files"].get(path) != digest:
                raise RuntimeError(f"frozen WebCraft file changed or disappeared: {path}")
    if not actual.get("experiment_ready"):
        raise RuntimeError("current WebCraft tree is not experiment-ready")
    return str(expected["webcraft_commit"]), str(actual["webcraft_commit"])


def pipeline_datasets() -> dict[str, list[dict[str, Any]]]:
    return {
        "all_public": jsonl(ROOT / "artifacts/main/all_public/episodes.jsonl")
        + jsonl(ROOT / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl"),
        "llm_only": jsonl(ROOT / "artifacts/main/llm_only/episodes.jsonl")
        + jsonl(ROOT / "artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl"),
        "oracle_reading": jsonl(ROOT / "artifacts/post_review/oracle_reading/episodes.jsonl"),
    }


def external_datasets() -> dict[str, list[dict[str, Any]]]:
    rows = {method: jsonl(path) for method, path in EXTERNAL_PATHS.items()}
    if any(len(group) != 110 for group in rows.values()):
        raise RuntimeError("external matrices are incomplete")
    return rows


def is_stale(vector: dict[str, Any], world: str) -> bool:
    return vector.get(world) is False and any(value is True for value in vector.values())


def pipeline_consequence(
    datasets: dict[str, list[dict[str, Any]]], vectors: dict[str, dict[str, Any]],
    nodes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    action_rows: list[dict[str, Any]] = []
    episode_rows: list[dict[str, Any]] = []
    for mode, records in datasets.items():
        for record in records:
            semantic = [
                (index, event) for index, event in enumerate(record["events"])
                if event.get("method") in PIPELINE_ACTIONS
            ]
            mechanisms = list(record.get("used_mechanism_ids", []))
            traces = list(record.get("policy_trace", []))
            if len(semantic) != len(mechanisms):
                raise RuntimeError("pipeline action/mechanism alignment changed")
            stale_by_event: dict[int, list[str]] = {}
            predicted: dict[int, set[str]] = {}
            mismatches: dict[int, bool] = {}
            for position, ((event_index, event), mechanism) in enumerate(zip(semantic, mechanisms)):
                if mechanism in vectors and is_stale(vectors[mechanism], record["world"]):
                    stale_by_event[event_index] = [mechanism]
                trace = traces[position] if position < len(traces) else {}
                try:
                    signature = json.loads(trace.get("action_signature", "null"))
                except json.JSONDecodeError:
                    signature = None
                if isinstance(signature, list) and len(signature) >= 3 and isinstance(signature[2], str):
                    predicted[event_index] = {signature[2].removeprefix("minecraft:")}
                if isinstance(trace.get("observed"), bool):
                    mismatches[event_index] = not trace["observed"]
                else:
                    mismatches[event_index] = event.get("success") is False
            actions = consequence_actions(
                record["events"], stale_by_event=stale_by_event, resource_nodes=nodes,
                predicted_products=predicted, mismatches=mismatches,
            )
            htr_all, htr_c = episode_htr(actions)
            accepted_all = int(record["metrics"]["HTR_all"])
            if htr_all != accepted_all:
                raise RuntimeError("consequence scorer changed stale-contact identity")
            plan = int(record["metrics"]["HTR_commit"])
            key = {name: record[name] for name in ("method", "condition", "world", "task_id", "seed")}
            episode_rows.append({
                "reading_mode": mode, **key, "HTR_all": htr_all,
                "HTR_commit_plan_source": plan, "HTR_commit_c": htr_c,
                "agreement": int(plan == htr_c), "stale_actions": len(actions),
                "consequence_commit_actions": sum(row["consequence_commit"] for row in actions),
            })
            role_by_event = {
                event_index: ("probe" if event.get("intent") == "probe" else "commit")
                for event_index, event in semantic
            }
            for row in actions:
                serial = {
                    ("action_method" if name == "method" else name): (
                        json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                    ) for name, value in row.items()
                }
                action_rows.append({
                    "reading_mode": mode, **key, **serial,
                    "plan_source_role": role_by_event[row["event_index"]],
                })
    return action_rows, episode_rows


def disagreement_reason(actions: list[dict[str, Any]], plan: int, consequence: int) -> str:
    if plan == consequence:
        return "agreement"
    witnesses = {
        witness for row in actions if int(row["consequence_commit"])
        for witness in json.loads(row["witnesses"])
    }
    if len(witnesses) > 1:
        return "multiple_witnesses"
    mapping = {
        "consumed_predicted_product": "consumed_after_plan_probe",
        "repeated_after_mismatch": "repeated_after_mismatch",
        "terminal_submission": "terminal_submission_after_probe",
    }
    if consequence:
        return mapping[next(iter(witnesses))] if witnesses else "multiple_witnesses"
    return "plan_commit_without_consequence"


def pipeline_comparisons(
    actions: list[dict[str, Any]], episodes: list[dict[str, Any]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    by_episode: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    key_fields = ("reading_mode", "method", "condition", "world", "task_id", "seed")
    for row in actions:
        by_episode[tuple(row[name] for name in key_fields)].append(row)
    disagreements = []
    for row in episodes:
        if row["agreement"]:
            continue
        key = tuple(row[name] for name in key_fields)
        disagreements.append({
            **{name: row[name] for name in key_fields},
            "HTR_commit_plan_source": row["HTR_commit_plan_source"],
            "HTR_commit_c": row["HTR_commit_c"],
            "reason": disagreement_reason(
                by_episode[key], row["HTR_commit_plan_source"], row["HTR_commit_c"],
            ),
            "stale_event_indices": ";".join(str(value["event_index"]) for value in by_episode[key]),
        })
    matrices = []
    summaries = []
    groupings: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        groupings[(row["reading_mode"], row["method"])].append(row)
        groupings[(row["reading_mode"], "__all_methods__")].append(row)
    for (mode, method), rows in sorted(groupings.items()):
        counts = Counter((row["HTR_commit_plan_source"], row["HTR_commit_c"]) for row in rows)
        for plan in (0, 1):
            for consequence in (0, 1):
                matrices.append({
                    "reading_mode": mode, "method_scope": method,
                    "plan_source": plan, "consequence": consequence,
                    "episodes": counts[(plan, consequence)],
                })
        diagonal = counts[(0, 0)] + counts[(1, 1)]
        summaries.append({
            "reading_mode": mode, "method": method, "episodes": len(rows),
            "plan_source_HTR_commit": mean(row["HTR_commit_plan_source"] for row in rows),
            "HTR_commit_c": mean(row["HTR_commit_c"] for row in rows),
            "diagonal": diagonal, "agreement_rate": diagonal / len(rows),
            "off_diagonal": len(rows) - diagonal,
        })
    return matrices, disagreements, summaries


def external_audit_index() -> dict[tuple[str, str, str, str, int, int], list[str]]:
    rows = csv_rows(ROOT / "reports/main/post_review/external_agents/rescore_v2/action_audit.csv")
    output: dict[tuple[str, str, str, str, int, int], list[str]] = {}
    for row in rows:
        agent = row["episode_method"]
        if agent not in EXTERNAL_PATHS:
            continue
        output[(agent, row["condition"], row["world"], row["task_id"],
                int(row["seed"]), int(row["event_index"]))] = json.loads(row["stale_candidate_ids"])
    return output


def external_consequence(
    datasets: dict[str, list[dict[str, Any]]], nodes: dict[str, str],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    audit = external_audit_index()
    action_rows = []
    episode_rows = []
    log_hashes = {}
    for method, records in datasets.items():
        for record in records:
            path = ROOT / record["action_log"]
            events = jsonl(path)
            log_hashes[str(path.relative_to(ROOT))] = hashlib.sha256(path.read_bytes()).hexdigest()
            stale_by_event = {}
            for index in range(len(events)):
                ids = audit.get((method, record["condition"], record["world"], record["task_id"],
                                 int(record["seed"]), index), [])
                if ids:
                    stale_by_event[index] = ids
            actions = consequence_actions(events, stale_by_event=stale_by_event, resource_nodes=nodes)
            htr_all, htr_c = episode_htr(actions)
            legacy = int(record["metrics"].get(
                "self_reported_HTR_commit", record["metrics"].get("HTR_commit", 0),
            ))
            key = {name: record[name] for name in ("method", "condition", "world", "task_id", "seed")}
            episode_rows.append({
                **key, "HTR_all_candidate_contact": htr_all,
                "HTR_commit_c": htr_c, "legacy_self_reported_HTR_commit": legacy,
                "stale_actions": len(actions),
                "consequence_commit_actions": sum(row["consequence_commit"] for row in actions),
                "estimand": "catalogue_stale_action_contact_with_observable_consequence",
            })
            for row in actions:
                serial = {
                    ("action_method" if name == "method" else name): (
                        json.dumps(value, sort_keys=True) if isinstance(value, (list, dict)) else value
                    ) for name, value in row.items()
                }
                action_rows.append({
                    **key, **serial,
                })
    summary = []
    for method, rows in sorted(_group(episode_rows, "method").items()):
        summary.append({
            "method": method, "episodes": len(rows),
            "HTR_all_candidate_contact": mean(row["HTR_all_candidate_contact"] for row in rows),
            "HTR_commit_c": mean(row["HTR_commit_c"] for row in rows),
            "legacy_self_reported_HTR_commit": mean(
                row["legacy_self_reported_HTR_commit"] for row in rows),
            "identified_stale_actions": sum(row["stale_actions"] for row in rows),
            "identified_commit_actions": sum(row["consequence_commit_actions"] for row in rows),
        })
    return action_rows, episode_rows, summary, log_hashes


def _group(rows: Iterable[dict[str, Any]], *fields: str) -> dict[Any, list[dict[str, Any]]]:
    output: dict[Any, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        key_tuple = tuple(row[field] for field in fields)
        output[key_tuple[0] if len(key_tuple) == 1 else key_tuple].append(row)
    return output


def adversarial_validation(nodes: dict[str, str]) -> list[dict[str, Any]]:
    base = {
        "schema_version": 2, "method": "mine", "params": {"nodeId": "iron_1"},
        "success": False, "intent": "probe", "item_flow": {"consumed": [], "produced": []},
        "cost": {"primary": 1},
    }
    actions = consequence_actions(
        [dict(base), dict(base)], stale_by_event={0: ["cf_adversarial"], 1: ["cf_adversarial"]},
        resource_nodes={**nodes, "iron_1": "iron_ore"}, mismatches={0: True, 1: True},
    )
    return [{
        "fixture": "all_actions_self_report_probe_repeated_after_mismatch",
        "all_intents": "probe", "HTR_all": episode_htr(actions)[0],
        "HTR_commit_c": episode_htr(actions)[1],
        "first_action_witnesses": ";".join(actions[0]["witnesses"]),
        "passed": int(episode_htr(actions) == (1, 1)),
    }]


def verify_all_after_negative(
    datasets: dict[str, list[dict[str, Any]]], nodes: dict[str, str],
) -> list[dict[str, Any]]:
    rows = []
    for mode, records in datasets.items():
        for record in records:
            if record["method"] != "verify_all":
                continue
            semantic = [(i, e) for i, e in enumerate(record["events"]) if e.get("method") in ACTION_METHODS]
            traces = record.get("policy_trace", [])
            for position, (event_index, event) in enumerate(semantic):
                trace = traces[position] if position < len(traces) else {}
                if trace.get("intent") != "probe" or trace.get("observed") is not False:
                    continue
                current = action_signature(event, nodes)
                later = semantic[position + 1:]
                repeated = next((i for i, e in later if action_signature(e, nodes) == current), None)
                submit = next((
                    (i, e) for i, e in enumerate(record["events"][event_index + 1:], event_index + 1)
                    if e.get("method") == "submit"
                ), None)
                if repeated is not None:
                    behavior = "continue_original_plan"
                elif not later and submit and submit[1].get("params", {}).get("result", {}).get("feasible") is False:
                    behavior = "abstain"
                elif later:
                    behavior = "replan"
                else:
                    behavior = "indeterminate"
                rows.append({
                    "reading_mode": mode, **{k: record[k] for k in ("condition", "world", "task_id", "seed")},
                    "event_index": event_index, "mechanism_id": trace.get("mechanism_id", ""),
                    "next_behavior": behavior,
                })
    return rows


def gold_path_transition(
    datasets: dict[str, list[dict[str, Any]]],
) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    source = [
        row for row in datasets["all_public"]
        if row["method"] == "naive_rag" and int(row["seed"]) == 20261006
    ]
    gold = {
        (row["condition"], row["world"], row["task_id"]): row
        for row in datasets["oracle_reading"] if row["method"] == "naive_rag"
    }
    closure = {
        (row["condition"], row["task_id"]): row
        for row in csv_rows(POST_OUT / "oracle_reading_closure.csv")
    }
    selected = []
    for row in source:
        semantic = [e for e in row["events"] if e.get("method") in ACTION_METHODS]
        failed = any(e.get("success") is False for e in semantic)
        cohort = "execution_failed_65" if not row["metrics"]["SR"] and failed else (
            "feasible_success_106" if row["metrics"]["SR"] and row["true_feasible"] else None
        )
        if cohort:
            selected.append((cohort, row))
    counts = Counter(cohort for cohort, _ in selected)
    if counts != Counter({"execution_failed_65": 65, "feasible_success_106": 106}):
        raise RuntimeError(f"Naive transition cohorts changed: {counts}")

    def category(row: dict[str, Any]) -> str:
        if row["metrics"]["SR"]:
            return "success"
        if float(row["metrics"]["primary_cost"]) >= 90:
            return "budget_exhausted"
        semantic = [e for e in row["events"] if e.get("method") in ACTION_METHODS]
        if row["reported_feasible"] is False:
            return "action_then_false_infeasible" if semantic else "no_plan_abstain"
        return "execution_failed"

    details = []
    for cohort, before in selected:
        key = (before["condition"], before["world"], before["task_id"])
        after = gold[key]
        after_category = category(after)
        reason = ""
        if after_category in {"no_plan_abstain", "action_then_false_infeasible"}:
            coverage = closure[(before["condition"], before["task_id"])]
            if int(coverage["supplied_mechanisms"]) < int(coverage["required_mechanisms"]):
                reason = "path_mechanism_absent_in_condition_corpus"
            elif before["metrics"]["SR"]:
                reason = "alternative_route_removed"
            else:
                reason = "other"
        details.append({
            "source_cohort": cohort, "condition": before["condition"], "world": before["world"],
            "task_id": before["task_id"], "source_terminal": category(before),
            "gold_path_terminal": after_category, "abstention_reason": reason,
        })
    summary = []
    grouped = _group(details, "source_cohort", "gold_path_terminal")
    for (cohort, result), rows in sorted(grouped.items()):
        summary.append({
            "source_cohort": cohort, "gold_path_terminal": result, "instances": len(rows),
            "path_absent": sum(row["abstention_reason"] == "path_mechanism_absent_in_condition_corpus" for row in rows),
            "alternative_removed": sum(row["abstention_reason"] == "alternative_route_removed" for row in rows),
            "other_abstention": sum(row["abstention_reason"] == "other" for row in rows),
        })
    return details, summary


def evidence_path(mode: str, record: dict[str, Any]) -> Path | None:
    if record["method"] in {"parametric", "probe_then_parametric", "oracle"}:
        return None
    if mode == "oracle_reading":
        return ROOT / "artifacts/post_review/oracle_reading/evidence" / f"{record['condition']}__{record['task_id']}.json"
    if record["method"] == "probe_then_rag":
        return ROOT / "artifacts/post_run_probe_then_rag" / mode / "evidence" / f"{record['condition']}__{record['task_id']}__{record['seed']}.json"
    return ROOT / "artifacts/main" / mode / "evidence" / f"{record['condition']}__{record['task_id']}__{record['seed']}.json"


def opportunity_from_bundle(
    bundle: dict[str, Any], stale_path: set[str], normalizer: Any,
) -> int:
    derived, _ = _claims(bundle, normalizer=normalizer)
    chunk_mechanisms: dict[str, set[str]] = defaultdict(set)
    allowed_chunks = {str(row["chunk_id"]) for row in bundle["search_rows"]}
    for claim in derived:
        chunk_mechanisms[str(claim.hit["chunk_id"])].add(str(claim.mechanism_id))
    events = bundle.get("reader", {}).get("search_events") or [
        {"chunk_ids": sorted(allowed_chunks)}
    ]
    return sum(len(stale_path & set().union(
        *(chunk_mechanisms.get(str(chunk), set()) for chunk in event.get("chunk_ids", []))
    )) for event in events)


def pipeline_normalized(
    datasets: dict[str, list[dict[str, Any]]], action_rows: list[dict[str, Any]],
    vectors: dict[str, dict[str, Any]], normalizer: Any,
) -> list[dict[str, Any]]:
    stale_actions = Counter()
    first_stale: dict[tuple[Any, ...], int] = {}
    for row in action_rows:
        key = tuple(row[name] for name in ("reading_mode", "method", "condition", "world", "task_id", "seed"))
        stale_actions[key] += 1
        first_stale[key] = min(first_stale.get(key, 10**9), int(row["semantic_index"]))
    details = []
    opportunity_cache: dict[tuple[Any, ...], int] = {}
    for mode, records in datasets.items():
        for record in records:
            key = (mode, record["method"], record["condition"], record["world"], record["task_id"], record["seed"])
            semantic = sum(e.get("method") in ACTION_METHODS for e in record["events"])
            stale_path = {
                claim for claim in record["conflict_mechanism_ids"]
                if claim in vectors and is_stale(vectors[claim], record["world"])
            }
            path = evidence_path(mode, record)
            opportunities = 0
            if path is not None:
                family = "probe_then_rag" if record["method"] == "probe_then_rag" else "shared_reader"
                cache_key = (mode, family, record["condition"], record["task_id"], record["seed"], record["world"])
                if cache_key not in opportunity_cache:
                    bundle = json.loads(path.read_text())
                    identified = record.get("temporal_filter", {}).get("identified_version")
                    if record["method"] == "probe_then_rag" and identified:
                        bundle = _probe_then_rag_filter_bundle(bundle, identified)
                    opportunity_cache[cache_key] = opportunity_from_bundle(bundle, stale_path, normalizer)
                opportunities = opportunity_cache[cache_key]
            details.append({
                "family": "pipeline", "reading_mode": mode, "method": record["method"],
                "condition": record["condition"], "world": record["world"], "task_id": record["task_id"],
                "seed": record["seed"], "semantic_actions": semantic,
                "stale_semantic_actions": stale_actions[key], "stale_opportunities": opportunities,
                "actions_before_first_stale": "" if key not in first_stale else first_stale[key],
            })
    return details


def external_normalized(
    datasets: dict[str, list[dict[str, Any]]], action_rows: list[dict[str, Any]],
    vectors: dict[str, dict[str, Any]], normalizer: Any,
) -> list[dict[str, Any]]:
    from corpus.claims_cache import ClaimsCache

    stale_actions = Counter()
    first_stale = {}
    for row in action_rows:
        key = tuple(row[name] for name in ("method", "condition", "world", "task_id", "seed"))
        stale_actions[key] += 1
        first_stale[key] = min(first_stale.get(key, 10**9), int(row["semantic_index"]))
    tasks = {row["id"]: row for row in jsonl(WEBCRAFT / "data/tasks.jsonl") if row.get("split") == "eval"}
    caches: dict[str, Any] = {}
    details = []
    for method, records in datasets.items():
        for record in records:
            events = jsonl(ROOT / record["action_log"])
            search_events = [event for event in events if event.get("method") == "search"]
            search_rows = [row for event in search_events for row in event.get("result", []) if isinstance(row, dict)]
            condition = record["condition"]
            if condition not in caches:
                caches[condition] = ClaimsCache(str(WEBCRAFT / "corpus/indexes/formal_p1" / condition))
            cache = caches[condition]
            unique_rows = {str(row["chunk_id"]): row for row in search_rows}
            claims = [claim for chunk in unique_rows for claim in cache.get(chunk)]
            bundle = {
                "search_rows": list(unique_rows.values()), "claims": claims,
                "mineable_nodes": [], "reader": {
                    "search_events": [{"chunk_ids": [str(row["chunk_id"]) for row in event.get("result", [])]}
                                      for event in search_events],
                },
            }
            stale_path = {
                claim for claim in tasks[record["task_id"]]["conflict_ids"]
                if claim in vectors and is_stale(vectors[claim], record["world"])
            }
            opportunities = opportunity_from_bundle(bundle, stale_path, normalizer) if search_rows else 0
            key = (method, condition, record["world"], record["task_id"], record["seed"])
            details.append({
                "family": "external", "reading_mode": "all_public", "method": method,
                "condition": condition, "world": record["world"], "task_id": record["task_id"],
                "seed": record["seed"],
                "semantic_actions": sum(e.get("method") in ACTION_METHODS for e in events),
                "stale_semantic_actions": stale_actions[key], "stale_opportunities": opportunities,
                "actions_before_first_stale": "" if key not in first_stale else first_stale[key],
            })
    return details


def normalized_summary(details: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for (family, mode, method), rows in sorted(_group(details, "family", "reading_mode", "method").items()):
        first = [int(row["actions_before_first_stale"]) for row in rows if row["actions_before_first_stale"] != ""]
        semantic = sum(row["semantic_actions"] for row in rows)
        stale = sum(row["stale_semantic_actions"] for row in rows)
        opportunities = sum(row["stale_opportunities"] for row in rows)
        opportunity_episodes = sum(row["stale_opportunities"] > 0 for row in rows)
        contacted_opportunity_episodes = sum(
            row["stale_opportunities"] > 0 and row["stale_semantic_actions"] > 0
            for row in rows
        )
        output.append({
            "family": family, "reading_mode": mode, "method": method, "episodes": len(rows),
            "stale_semantic_actions": stale, "semantic_actions": semantic,
            "stale_action_fraction": stale / semantic if semantic else "",
            "stale_opportunities": opportunities,
            "opportunities_per_episode": opportunities / len(rows),
            "action_contacts_per_retrieval_opportunity": stale / opportunities if opportunities else "",
            "episodes_with_stale_opportunity": opportunity_episodes,
            "episodes_with_contact_and_opportunity": contacted_opportunity_episodes,
            "contact_opportunity_episode_ratio": (
                contacted_opportunity_episodes / opportunity_episodes
                if opportunity_episodes else ""
            ),
            "episodes_with_stale_action": len(first),
            "actions_before_first_stale_mean": mean(first) if first else "",
            "actions_before_first_stale_median": statistics.median(first) if first else "",
            "actions_before_first_stale_min": min(first) if first else "",
            "actions_before_first_stale_max": max(first) if first else "",
        })
    return output


def mechanism_family(claim: dict[str, Any]) -> str:
    assertion = claim["assertion"]
    if assertion.get("kind") == "drop":
        return "ore_drop"
    if assertion.get("kind") == "name":
        return "name"
    if assertion.get("kind") == "recipe" and assertion.get("station") == "furnace":
        return "smelting"
    if assertion.get("kind") in {"recipe", "concrete_grid"}:
        return "recipe"
    return "other"


def bootstrap_accuracy(rows: list[dict[str, str]], iterations: int = 10000) -> tuple[float, float]:
    grouped = _group(rows, "condition", "world", "task_id", "seed")
    units = list(grouped.values())
    correct = np.array([sum(int(row["correct"]) for row in unit) for unit in units], dtype=float)
    total = np.array([len(unit) for unit in units], dtype=float)
    rng = np.random.default_rng(20261005)
    draws = []
    for start in range(0, iterations, 500):
        n = min(500, iterations - start)
        indices = rng.integers(0, len(units), size=(n, len(units)))
        draws.extend((correct[indices].sum(axis=1) / total[indices].sum(axis=1)).tolist())
    return tuple(float(value) for value in np.quantile(draws, [0.025, 0.975]))


def iaa_detail() -> list[dict[str, Any]]:
    detail = csv_rows(POST_OUT / "measurement_v2/iaa_detail.csv")
    conflicts = {row["id"]: row for row in jsonl(WEBCRAFT / "data/conflicts.jsonl")}
    for row in detail:
        row["mechanism_family"] = mechanism_family(conflicts[row["claim_id"]])
    output = []
    grouped = _group(detail, "agent_family", "reading_mode", "method")
    for (agent, mode, method), method_rows in sorted(grouped.items()):
        families = [("all", method_rows)] + sorted(
            _group(method_rows, "mechanism_family").items(), key=lambda item: item[0],
        )
        for family, rows in families:
            truth = Counter(row["truth"] for row in rows)
            low, high = bootstrap_accuracy(rows)
            answered = [row for row in rows if not int(row["unknown"])]
            output.append({
                "agent_family": agent, "reading_mode": mode, "method": method,
                "mechanism_family": family, "queries": len(rows),
                "supported_truth": truth["True"], "unsupported_truth": truth["False"],
                "supported_truth_fraction": truth["True"] / len(rows),
                "unsupported_truth_fraction": truth["False"] / len(rows),
                "majority_class_accuracy": max(truth.values()) / len(rows),
                "conditional_accuracy_all_queries": mean(int(row["correct"]) for row in rows),
                "conditional_accuracy_answered": mean(int(row["correct"]) for row in answered) if answered else "",
                "unknown_rate": mean(int(row["unknown"]) for row in rows),
                "episode_bootstrap_ci_low": low, "episode_bootstrap_ci_high": high,
                "bootstrap_iterations": 10000, "bootstrap_seed": 20261005,
            })
    return output


def rerun_agreement() -> list[dict[str, Any]]:
    first = [row for row in csv_rows(
        POST_OUT / "external_agents/rescore_v2/episode_metrics.csv"
    ) if row["method"] == "direct_llm"]
    rerun = [row for row in csv_rows(
        POST_OUT / "external_second_model_probe/episode_metrics.csv"
    ) if row["method"] == "direct_llm"]
    fields = ("condition", "world", "task_id", "seed")
    left = {tuple(row[name] for name in fields): row for row in first}
    right = {tuple(row[name] for name in fields): row for row in rerun}
    if set(left) != set(right) or len(left) != 110:
        raise RuntimeError("Direct same-model paired matrix mismatch")
    output = []
    for metric, column in (("SR", "SR"), ("HTR_all", "legacy_HTR_all")):
        counts = Counter((int(left[key][column]), int(right[key][column])) for key in left)
        for before in (0, 1):
            for after in (0, 1):
                output.append({
                    "comparison": "direct_llm_first_vs_glm52_rerun", "metric": metric,
                    "first": before, "rerun": after, "instances": counts[(before, after)],
                    "paired_total": len(left),
                    "agreement_rate": (counts[(0, 0)] + counts[(1, 1)]) / len(left),
                })
    joint = sum(
        left[key]["SR"] == right[key]["SR"]
        and left[key]["legacy_HTR_all"] == right[key]["legacy_HTR_all"]
        for key in left
    )
    output.append({
        "comparison": "direct_llm_first_vs_glm52_rerun", "metric": "joint_SR_and_HTR_all",
        "first": "same", "rerun": "same", "instances": joint, "paired_total": len(left),
        "agreement_rate": joint / len(left),
    })
    return output


def score_opr_ovr(
    datasets: dict[str, list[dict[str, Any]]], vectors: dict[str, dict[str, Any]],
) -> list[dict[str, Any]]:
    from metrics.episode import aggregate, episode_metrics

    tasks = {row["id"]: row for row in jsonl(WEBCRAFT / "data/tasks.jsonl") if row.get("split") == "eval"}
    selected_methods = {"verify_all", "probe_then_parametric", "probe_then_rag"}
    output = []
    for mode, records in datasets.items():
        scored: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for row in records:
            if row["method"] not in selected_methods:
                continue
            task = tasks[row["task_id"]]
            mechanisms = iter(row["used_mechanism_ids"])
            event_vectors = {}
            for index, event in enumerate(row["events"]):
                if event.get("method") not in ACTION_METHODS:
                    continue
                mechanism = next(mechanisms)
                if mechanism in vectors:
                    event_vectors[index] = vectors[mechanism]
            initial = {
                item["name"]: int(item["count"])
                for item in task["standard_items"] + task.get("task_supplements", [])
            }
            metrics = episode_metrics(
                row["events"], goal=task["target"], world=row["world"],
                true_feasible=row["true_feasible"], success=bool(row["metrics"]["SR"]),
                reported_feasible=row["reported_feasible"], version_hashes={},
                mechanism_vectors=event_vectors, oracle_cost=task["oracle_cost"][row["world"]],
                task_type=task["type"], initial_inventory=initial,
            )
            metrics["include_opr"] = task["type"] == "efficiency" and row["world"] in {"1.16.5", "1.19"}
            scored[row["method"]].append(metrics)
        for method, rows in sorted(scored.items()):
            opr = [row["OPR"] for row in rows if row["include_opr"] and row["OPR"] is not None]
            ovr = [row["OVR"] for row in rows if row["OVR"] is not None]
            output.append({
                "reading_mode": mode, "method": method,
                "OPR": mean(opr) if opr else "", "OPR_n": len(opr),
                "OPR_scope": "efficiency tasks in worlds 1.16.5 and 1.19",
                "OVR": mean(ovr) if ovr else "", "OVR_n": len(ovr),
                "OVR_scope": "classified craft_grid/check_name validation candidates",
            })
    return output


def corrected_sr_intervals(datasets: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    records = [EpisodeRecord.from_dict(row) for row in datasets["all_public"]]
    methods = ("naive_rag", "majority_rag", "truthfinder_rag")
    scopes = [("world_1.16.5_all_cells", None)] + [
        (f"cell_{condition}", condition) for condition in
        ("matched_1.16.5", "mismatched_new2old", "mixed", "polluted")
    ]
    accepted = csv_rows(ROOT / "reports/main/acceptance_exports/all_public_11_cells.csv")
    accepted_index = {(r["method"], r["condition"], r["world"]): r for r in accepted}
    output = []
    for scope, condition in scopes:
        by_method = {}
        for method in methods:
            rows = [row for row in records if row.method == method and row.world == "1.16.5"
                    and (condition is None or row.condition == condition)]
            by_method[method] = sorted(rows, key=lambda row: (row.condition, row.task_id, row.seed))
        vectors = [[row.sr for row in by_method[method]] for method in methods]
        if not all(vector == vectors[0] for vector in vectors[1:]):
            raise RuntimeError("aggregation baselines do not have identical SR vectors")
        base = by_method[methods[0]]
        clusters = _components(base)
        rng = np.random.default_rng(20261005)
        estimates = []
        for _ in range(10000):
            draw = rng.integers(0, len(clusters), size=len(clusters))
            sampled = [row.sr for index in draw for row in clusters[int(index)]]
            estimates.append(float(np.mean(sampled)))
        low, high = (float(x) for x in np.quantile(estimates, [0.025, 0.975]))
        for method in methods:
            old = accepted_index.get((method, condition, "1.16.5")) if condition else None
            output.append({
                "scope": scope, "method": method, "episodes": len(by_method[method]),
                "SR": mean(row.sr for row in by_method[method]),
                "old_ci_low": "" if old is None else old["SR_ci_low"],
                "old_ci_high": "" if old is None else old["SR_ci_high"],
                "corrected_shared_draw_ci_low": low, "corrected_shared_draw_ci_high": high,
                "paired_vector_identical": 1, "bootstrap_iterations": 10000,
                "bootstrap_seed": 20261005,
                "cause": "reporting._cluster_interval consumed one sequential RNG stream per method and metric; corrected audit reuses identical cluster draws for identical vectors",
            })
    return output


def probe_then_inputs() -> str:
    source = ROOT / "src/arbiter/evaluation/p6_execution.py"
    digest = hashlib.sha256(source.read_bytes()).hexdigest()
    return "\n".join([
        "# Probe-then-RAG information inputs", "",
        "This is a post-review disclosure over the frozen implementation; no episode was rerun.", "",
        "- **Probe target source:** the two separator mechanisms and prerequisite actions are hard-coded in "
        "`p6_execution.py::_probe_then_rag_identify`, selected by the post-run amendment rather than discovered "
        "from retrieved text at runtime. The targets are iron-ore drop behavior and `turtle_scute` name resolution.",
        "- **Outcome-to-version mapping:** evaluator output is not queried at runtime, but the implementation "
        "hard-codes `raw_iron -> post-1.16`, no `raw_iron -> 1.16.5`, and then "
        "`turtle_scute` resolvable/unresolvable -> `1.21.11`/`1.19`. It therefore contains the candidate "
        "version labels and a hand-specified separator-to-version mapping; this mapping does not come from "
        "the retrieved corpus text.",
        "- **Temporal filtering:** after identification, hard-coded version labels index frozen cutoffs copied "
        "from `webcraft/corpus/release_times.json` and filter public search rows by timestamp.",
        "- **`report_version` string:** the literal identified label (`1.16.5`, `1.19`, or `1.21.11`) is passed "
        "through the public adapter; the simulator logs only its hash.",
        "- **Runtime exclusions:** no truth vector, hidden simulator state, or evaluator-provided mapping is read "
        "inside the run. Nevertheless, because the candidate list and outcome mapping are encoded in the "
        "baseline, Probe-then-RAG must be labelled a **version-known control**, not a version-blind method.", "",
        f"Implementation SHA-256: `{digest}`.", "",
    ])


def report(
    summaries: list[dict[str, Any]], external: list[dict[str, Any]],
    transition: list[dict[str, Any]], verify: list[dict[str, Any]],
) -> str:
    diagonal = [row for row in summaries if row["method"] == "__all_methods__"]
    transition_text = ", ".join(
        f"{row['source_cohort']} -> {row['gold_path_terminal']}: {row['instances']}"
        for row in transition
    )
    verify_counts = Counter(row["next_behavior"] for row in verify)
    return "\n".join([
        "# Consequence-based measurement reanalysis", "",
        "All outputs are deterministic replays of immutable logs. No model call or episode execution occurred.", "",
        "## Pipeline agreement", "",
        *[f"- {row['reading_mode']}: diagonal {row['diagonal']}/{row['episodes']} "
          f"({row['agreement_rate']:.6f})." for row in diagonal], "",
        "## External point estimates", "",
        *[f"- {row['method']}: HTR_commit^c={row['HTR_commit_c']:.6f}; "
          f"legacy self-report={row['legacy_self_reported_HTR_commit']:.6f}."
          for row in external], "",
        "The external value is a catalogue-stale action-contact estimand with observable downstream "
        "consequences; it does not recover the model's latent belief or intended assertion.", "",
        "## Gold-path transitions", "", transition_text, "",
        "## Verify-All after negative probes", "",
        ", ".join(f"{key}={value}" for key, value in sorted(verify_counts.items())), "",
        "## Probe-then-RAG disclosure", "",
        "The baseline is version-known: candidate version labels and the probe-outcome mapping are hard-coded, "
        "although runtime truth vectors and evaluator mappings are not read.", "",
    ])


def main() -> None:
    frozen_commit, current_commit = verify_frozen_pin_subset()
    if str(WEBCRAFT) not in sys.path:
        sys.path.insert(0, str(WEBCRAFT))
    conflicts = load_conflicts(WEBCRAFT / "data/conflicts.jsonl")
    vectors = {row["id"]: row["truth_vector"] for row in conflicts}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    normalizer, _ = _compiler(WEBCRAFT)
    pipeline = pipeline_datasets()
    external = external_datasets()

    pipeline_actions, pipeline_episodes = pipeline_consequence(pipeline, vectors, nodes)
    matrices, disagreements, pipeline_summary = pipeline_comparisons(
        pipeline_actions, pipeline_episodes,
    )
    external_actions, external_episodes, external_summary, log_hashes = external_consequence(
        external, nodes,
    )
    verify = verify_all_after_negative(pipeline, nodes)
    transitions, transition_summary = gold_path_transition(pipeline)
    normalized_details = pipeline_normalized(
        pipeline, pipeline_actions, vectors, normalizer,
    ) + external_normalized(external, external_actions, vectors, normalizer)

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "pipeline_actions.csv", pipeline_actions)
    write_csv(OUT / "pipeline_episodes.csv", pipeline_episodes)
    write_csv(OUT / "pipeline_summary.csv", pipeline_summary)
    write_csv(OUT / "consistency_matrix.csv", matrices)
    write_csv(OUT / "disagreements.csv", disagreements or [{
        "reading_mode": "none", "method": "none", "condition": "none", "world": "none",
        "task_id": "none", "seed": "", "HTR_commit_plan_source": "", "HTR_commit_c": "",
        "reason": "no_disagreements", "stale_event_indices": "",
    }])
    write_csv(OUT / "external_actions.csv", external_actions)
    write_csv(OUT / "external_episodes.csv", external_episodes)
    write_csv(OUT / "external_summary.csv", external_summary)
    write_csv(OUT / "adversarial_validation.csv", adversarial_validation(nodes))
    write_csv(OUT / "verify_all_after_negative.csv", verify)
    write_csv(OUT / "transition_gold_path.csv", transitions)
    write_csv(OUT / "transition_gold_path_summary.csv", transition_summary)
    write_csv(OUT / "htr_normalized_detail.csv", normalized_details)
    write_csv(OUT / "htr_normalized.csv", normalized_summary(normalized_details))
    write_csv(OUT / "iaa_detail.csv", iaa_detail())
    write_csv(OUT / "rerun_agreement.csv", rerun_agreement())
    write_csv(OUT / "opr_ovr.csv", score_opr_ovr(pipeline, vectors))
    write_csv(OUT / "sr_interval_audit.csv", corrected_sr_intervals(pipeline))
    (OUT / "probe_then_rag_inputs.md").write_text(probe_then_inputs())
    (OUT / "REPORT.md").write_text(report(
        pipeline_summary, external_summary, transition_summary, verify,
    ))
    provenance = {
        "status": "POST_REVIEW_OFFLINE_REANALYSIS_COMPLETE",
        "prereg_commit": "110efb8",
        "llm_calls": 0,
        "pipeline_episodes": len(pipeline_episodes),
        "external_episodes": len(external_episodes),
        "external_action_log_sha256": log_hashes,
        "webcraft_frozen_pin_commit": frozen_commit,
        "webcraft_current_read_only_commit": current_commit,
    }
    (OUT / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "status": provenance["status"], "pipeline_episodes": len(pipeline_episodes),
        "external_episodes": len(external_episodes), "outputs": str(OUT),
    }, sort_keys=True))


if __name__ == "__main__":
    main()
