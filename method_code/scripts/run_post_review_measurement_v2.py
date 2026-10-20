"""Deterministic post-review measurement replay (2026-10-10 amendment).

This evaluator-only script never enters an agent loop. It reads immutable episode
and evidence records, recomputes measurement fields, and writes paper-audit tables.
"""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

from arbiter.agent.aggregation import (
    arbiter, majority_rag, naive_rag, parametric, truthfinder_rag, verify_all,
)
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes
from arbiter.evaluation.p6_execution import _claims, _compiler, _probe_then_rag_filter_bundle
from arbiter.evaluation.post_review_measurement import (
    TERMINAL_PRIORITY, external_self_reported, pipeline_plan_source,
    stable_identification_cost, terminal,
)
from arbiter.pinning import verify_webcraft_pin
from run_main import load_prior_bundle


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
MAIN = ROOT / "artifacts/main"
POST = ROOT / "artifacts/post_run_probe_then_rag"
ORACLE = ROOT / "artifacts/post_review/oracle_reading"
EXTERNAL = ROOT / "artifacts/post_review/external_agents/final"
OUT = ROOT / "reports/main/post_review/measurement_v2"
MODES = ("all_public", "llm_only", "oracle_reading")
EXTERNAL_PATHS = {
    "direct_llm": EXTERNAL / "direct_llm_assembled/episodes.jsonl",
    "react": EXTERNAL / "react_assembled/episodes.jsonl",
    "reflexion": EXTERNAL / "reflexion_assembled/episodes.jsonl",
    "adam": EXTERNAL / "adam/episodes.jsonl",
    "xenon": EXTERNAL / "xenon_assembled/episodes.jsonl",
}


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(name: str, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise ValueError(f"empty report: {name}")
    OUT.mkdir(parents=True, exist_ok=True)
    fields = list(rows[0])
    if any(set(row) != set(fields) for row in rows):
        raise ValueError(f"inconsistent fields: {name}")
    with (OUT / name).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def mean(values: Iterable[float]) -> float:
    rows = list(values)
    return sum(rows) / len(rows) if rows else 0.0


def pipeline_datasets() -> dict[str, list[dict[str, Any]]]:
    return {
        "all_public": read_jsonl(MAIN / "all_public/episodes.jsonl")
        + read_jsonl(POST / "all_public/episodes.jsonl"),
        "llm_only": read_jsonl(MAIN / "llm_only/episodes.jsonl")
        + read_jsonl(POST / "llm_only/episodes.jsonl"),
        "oracle_reading": read_jsonl(ORACLE / "episodes.jsonl"),
    }


def external_datasets() -> dict[str, list[dict[str, Any]]]:
    output = {method: read_jsonl(path) for method, path in EXTERNAL_PATHS.items()}
    if any(len(rows) != 110 for rows in output.values()):
        raise RuntimeError("external assembled matrices must each contain 110 episodes")
    return output


def evidence(mode: str, record: dict[str, Any]) -> dict[str, Any] | None:
    if record["method"] in {"parametric", "probe_then_parametric", "oracle"}:
        return None
    if mode == "oracle_reading":
        path = ORACLE / "evidence" / f"{record['condition']}__{record['task_id']}.json"
    elif record["method"] == "probe_then_rag":
        path = POST / mode / "evidence" / f"{record['condition']}__{record['task_id']}__{record['seed']}.json"
    else:
        path = MAIN / mode / "evidence" / f"{record['condition']}__{record['task_id']}__{record['seed']}.json"
    bundle = json.loads(path.read_text())
    identified = record.get("temporal_filter", {}).get("identified_version")
    if record["method"] == "probe_then_rag" and identified:
        bundle = _probe_then_rag_filter_bundle(bundle, identified)
    return bundle


def aggregate(method: str, bundle: dict[str, Any], normalizer: Any) -> Any | None:
    derived, _ = _claims(bundle, normalizer=normalizer)
    if not derived:
        return None
    if method in {"naive_rag", "arbiter_shell_naive", "probe_then_rag"}:
        return naive_rag(derived)
    if method == "majority_rag":
        return majority_rag(derived)
    if method == "truthfinder_rag":
        return truthfinder_rag(derived)
    if method == "verify_all":
        return verify_all(derived)
    if method == "arbiter":
        sources = {(row.hit["source_site"], row.hit["page_title"], row.hit["revid"])
                   for row in derived}
        return arbiter(derived) if len(sources) >= 2 else naive_rag(derived)
    raise ValueError(f"unsupported aggregation method: {method}")


def plan_source_reports(
    datasets: dict[str, list[dict[str, Any]]], vectors: dict[str, dict[str, bool | None]],
) -> dict[str, Any]:
    details: list[dict[str, Any]] = []
    episodes: list[dict[str, Any]] = []
    for mode, records in datasets.items():
        for record in records:
            scored, actions = pipeline_plan_source(record, vectors)
            key = {field: record[field] for field in ("method", "condition", "world", "task_id", "seed")}
            episodes.append({"reading_mode": mode, **key, **scored,
                             "self_reported_HTR_commit": record["metrics"]["HTR_commit"]})
            details.extend({"reading_mode": mode, **key, **row} for row in actions)
    write_csv("plan_source_actions.csv", details)
    write_csv("plan_source_episodes.csv", episodes)
    summary = []
    grouped: dict[tuple[str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in episodes:
        grouped[(row["reading_mode"], row["method"])].append(row)
    for (mode, method), rows in sorted(grouped.items()):
        summary.append({
            "reading_mode": mode, "method": method, "episodes": len(rows),
            "HTR_commit": mean(row["HTR_commit"] for row in rows),
            "HTR_all": mean(row["HTR_all"] for row in rows),
            "self_reported_HTR_commit": mean(row["self_reported_HTR_commit"] for row in rows),
            "degenerate_first_use_HTR_commit": mean(
                row["degenerate_first_use_HTR_commit"] for row in rows),
            "probe_count_mean": mean(row["probe_count"] for row in rows),
            "probe_cost_mean": mean(row["probe_cost"] for row in rows),
        })
    write_csv("plan_source_summary.csv", summary)
    return {"episodes": len(episodes), "actions": len(details), "rows": summary}


def external_reports(
    datasets: dict[str, list[dict[str, Any]]], conflicts: tuple[dict[str, Any], ...],
    nodes: dict[str, str],
) -> dict[str, Any]:
    episode_rows: list[dict[str, Any]] = []
    action_rows: list[dict[str, Any]] = []
    adam_rows: list[dict[str, Any]] = []
    for method, records in datasets.items():
        for record in records:
            events = read_jsonl(ROOT / record["action_log"])
            scored, details = external_self_reported(record, events, conflicts, nodes)
            key = {field: record[field] for field in ("method", "condition", "world", "task_id", "seed")}
            episode_rows.append({"reading_mode": "all_public", **key, **scored})
            action_rows.extend({"reading_mode": "all_public", **key,
                                **{k: json.dumps(v, sort_keys=True) if isinstance(v, list) else v
                                   for k, v in row.items()}}
                               for row in details)
            if method == "adam":
                probes = [event for event in events if event.get("intent") == "probe"]
                counts = Counter(
                    "craft" if event.get("method") == "craft_grid"
                    else event.get("method") if event.get("method") in {"check_name", "mine", "inventory"}
                    else "other"
                    for event in probes
                )
                paid = [event for event in probes if event.get("method") in ACTION_METHODS]
                adam_rows.append({
                    **key, "check_name": counts["check_name"], "mine": counts["mine"],
                    "craft": counts["craft"], "inventory": counts["inventory"],
                    "other": counts["other"],
                    "other_types": ";".join(sorted({str(event.get("method")) for event in probes
                                                      if event.get("method") not in ACTION_METHODS
                                                      and event.get("method") != "inventory"})),
                    "legacy_probe_count": len(probes),
                    "free_probe_count": sum(float(event["cost"]["primary"]) == 0 for event in probes),
                    "corrected_probe_count": len(paid),
                    "corrected_probe_cost": sum(float(event["cost"]["primary"]) for event in paid),
                })
    write_csv("external_self_reported_episodes.csv", episode_rows)
    if action_rows:
        write_csv("external_probe_consistency_actions.csv", action_rows)
    write_csv("adam_probe_audit.csv", adam_rows)
    summary = []
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in episode_rows:
        grouped[row["method"]].append(row)
    for method, rows in sorted(grouped.items()):
        summary.append({
            "method": method, "episodes": len(rows),
            "HTR_commit_self_reported": mean(row["HTR_commit"] for row in rows),
            "HTR_all": mean(row["HTR_all"] for row in rows),
            "degenerate_first_use_HTR_commit": mean(
                row["degenerate_first_use_HTR_commit"] for row in rows),
            "probe_then_later_commit_episodes": sum(row["consistency_violation"] for row in rows),
            "stale_probe_then_later_commit_episodes": sum(
                row["stale_consistency_violation"] for row in rows),
            "violating_claims": sum(row["violation_claims"] for row in rows),
        })
    write_csv("external_self_reported_summary.csv", summary)
    adam_summary = {
        "episodes": len(adam_rows),
        **{name: sum(row[name] for row in adam_rows)
           for name in ("check_name", "mine", "craft", "inventory", "other",
                        "legacy_probe_count", "free_probe_count", "corrected_probe_count",
                        "corrected_probe_cost")},
        "episodes_with_legacy_probe_but_zero_corrected_cost": sum(
            row["legacy_probe_count"] > 0 and row["corrected_probe_cost"] == 0
            for row in adam_rows),
    }
    write_csv("adam_probe_summary.csv", [adam_summary])
    return {"rows": summary, "adam": adam_summary}


def final_observations(record: dict[str, Any]) -> dict[str, bool]:
    # These are the only frozen execution strategies that mutate ContextBelief.
    if record["method"] not in {"arbiter", "verify_all", "probe_then_parametric"}:
        return {}
    output: dict[str, bool] = {}
    for trace in record.get("policy_trace", []):
        mechanism = trace.get("mechanism_id", trace.get("separator"))
        if (isinstance(mechanism, str) and isinstance(trace.get("observed"), bool)
                and not trace.get("resource_failure", False)):
            output[mechanism] = trace["observed"]
    return output


def iaa_reports(
    datasets: dict[str, list[dict[str, Any]]], external: dict[str, list[dict[str, Any]]],
    conflicts: tuple[dict[str, Any], ...], normalizer: Any,
) -> dict[str, Any]:
    vectors = {row["id"]: row["truth_vector"] for row in conflicts}
    prior, _ = load_prior_bundle(ROOT / "artifacts/prior/frozen_prior_bundle.json")
    prior_result = parametric(prior["samples"])
    cache: dict[tuple[Any, ...], Any] = {}
    detail: list[dict[str, Any]] = []
    for mode, records in datasets.items():
        for record in records:
            method = record["method"]
            if method in {"parametric", "probe_then_parametric"}:
                result = prior_result
            elif method == "oracle":
                result = None
            else:
                bundle = evidence(mode, record)
                bundle_hash = hashlib.sha256(
                    json.dumps(bundle, sort_keys=True, separators=(",", ":")).encode()
                ).hexdigest()
                # The two deterministic seeds have byte-identical evidence. Naive,
                # same-shell naive and Verify-All also share the same final belief.
                family = (
                    "naive_belief" if method in {
                        "naive_rag", "arbiter_shell_naive", "verify_all", "probe_then_rag",
                    } else method
                )
                key = (bundle_hash, family)
                if key not in cache:
                    representative = "naive_rag" if family == "naive_belief" else method
                    cache[key] = aggregate(representative, bundle, normalizer)
                result = cache[key]
            observed = final_observations(record)
            for claim in record["conflict_mechanism_ids"]:
                if claim in observed:
                    answer: bool | str = observed[claim]
                    source = "executed_observation"
                elif method == "oracle":
                    answer = vectors[claim][record["world"]]
                    source = "oracle_belief"
                elif result is not None and claim in result.belief.phi:
                    answer = result.belief.mechanism_probability(claim) >= 0.5
                    source = "final_belief"
                else:
                    answer = "unknown"
                    source = "unrepresented"
                truth = vectors[claim][record["world"]]
                detail.append({
                    "agent_family": "pipeline", "reading_mode": mode, "method": method,
                    "condition": record["condition"], "world": record["world"],
                    "task_id": record["task_id"], "seed": record["seed"],
                    "claim_id": claim, "answer": answer, "truth": truth,
                    "source": source, "correct": int(answer != "unknown" and answer == truth),
                    "unknown": int(answer == "unknown"),
                })
    # External traces do not persist a full belief table. Replayed adjudications
    # are usable; every unobserved IA query is conservatively unknown.
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    from arbiter.evaluation.external_scoring import _matches
    for method, records in external.items():
        for record in records:
            answers: dict[str, bool] = {}
            for event in read_jsonl(ROOT / record["action_log"]):
                if event.get("method") not in ACTION_METHODS:
                    continue
                for row in conflicts:
                    if _matches(event, row["assertion"], nodes):
                        result = event.get("result", {})
                        observed = (result.get("resolvable") if event["method"] == "check_name"
                                    else event.get("success"))
                        if isinstance(observed, bool):
                            answers[row["id"]] = observed
            task_claims = next((row["conflict_mechanism_ids"] for row in datasets["all_public"]
                                if row["task_id"] == record["task_id"]), [])
            for claim in task_claims:
                answer = answers.get(claim, "unknown")
                truth = vectors[claim][record["world"]]
                detail.append({
                    "agent_family": "external_action_replay", "reading_mode": "all_public",
                    "method": method, "condition": record["condition"], "world": record["world"],
                    "task_id": record["task_id"], "seed": record["seed"], "claim_id": claim,
                    "answer": answer, "truth": truth,
                    "source": "executed_observation" if answer != "unknown" else "unrepresented",
                    "correct": int(answer != "unknown" and answer == truth),
                    "unknown": int(answer == "unknown"),
                })
    write_csv("iaa_detail.csv", detail)
    summary = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in detail:
        grouped[(row["agent_family"], row["reading_mode"], row["method"])].append(row)
    for (family, mode, method), rows in sorted(grouped.items()):
        answered = [row for row in rows if not row["unknown"]]
        summary.append({
            "agent_family": family, "reading_mode": mode, "method": method,
            "queries": len(rows), "answered": len(answered),
            "accuracy_all": mean(row["correct"] for row in rows),
            "accuracy_answered": mean(row["correct"] for row in answered),
            "unknown_rate": mean(row["unknown"] for row in rows),
        })
    write_csv("iaa_summary.csv", summary)
    return {"rows": summary}


def ic_reports(
    datasets: dict[str, list[dict[str, Any]]], external: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    detail = []
    for mode, records in datasets.items():
        for record in records:
            expected = hashlib.sha256(record["world"].encode()).hexdigest()
            status, cost = stable_identification_cost(record["events"], expected)
            detail.append({
                "agent_family": "pipeline", "reading_mode": mode, "method": record["method"],
                "condition": record["condition"], "world": record["world"],
                "task_id": record["task_id"], "seed": record["seed"],
                "status": status, "IC": "" if cost is None else cost,
            })
    for method, records in external.items():
        for record in records:
            detail.append({
                "agent_family": "external", "reading_mode": "all_public", "method": method,
                "condition": record["condition"], "world": record["world"],
                "task_id": record["task_id"], "seed": record["seed"],
                "status": "report_version_not_exposed", "IC": "",
            })
    write_csv("ic_detail.csv", detail)
    summary = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in detail:
        grouped[(row["agent_family"], row["reading_mode"], row["method"])].append(row)
    for (family, mode, method), rows in sorted(grouped.items()):
        found = [float(row["IC"]) for row in rows if row["IC"] != ""]
        summary.append({
            "agent_family": family, "reading_mode": mode, "method": method,
            "episodes": len(rows), "identified": len(found),
            "identification_rate": len(found) / len(rows),
            "IC_mean_identified": mean(found) if found else "",
            "no_report": sum(row["status"] in {"no_report", "report_version_not_exposed"}
                             for row in rows),
            "never_stably_correct": sum(row["status"] == "never_stably_correct" for row in rows),
        })
    write_csv("ic_summary.csv", summary)
    return {"rows": summary}


def terminal_reports(datasets: dict[str, list[dict[str, Any]]]) -> dict[str, Any]:
    write_csv("terminal_priority.csv", [
        {"priority": index + 1, "terminal": name, "rule": rule}
        for index, (name, rule) in enumerate(TERMINAL_PRIORITY)
    ])
    detail = []
    for mode, records in datasets.items():
        for record in records:
            detail.append({
                "reading_mode": mode, "method": record["method"],
                "condition": record["condition"], "world": record["world"],
                "task_id": record["task_id"], "seed": record["seed"],
                "terminal": terminal(record),
            })
    counts: dict[tuple[str, str], Counter[str]] = defaultdict(Counter)
    for row in detail:
        counts[(row["reading_mode"], row["method"])][row["terminal"]] += 1
    categories = ("solved", "correctly_infeasible", "incorrectly_infeasible",
                  "plan_not_found", "execution_failed", "budget_exhausted")
    summary = [{"reading_mode": mode, "method": method,
                "episodes": sum(counter.values()),
                **{name: counter[name] for name in categories}}
               for (mode, method), counter in sorted(counts.items())]
    write_csv("terminal_distribution.csv", summary)
    return {"rows": summary}


def aggregation_and_count_provenance() -> dict[str, Any]:
    rows = [row for row in read_csv(ROOT / "reports/main/acceptance_exports/aggregation_candidates.csv")
            if row["row_type"] == "detail"]
    output = []
    counts = Counter()
    dedup: dict[tuple[str, str, str, str], str] = {}
    for row in rows:
        comparison = row["selection_comparison"]
        counts[comparison] += 1
        key = (row["condition"], row["world"], row["task_id"], row["required_mechanism_id"])
        previous = dedup.setdefault(key, comparison)
        if previous != comparison:
            raise RuntimeError("aggregation comparison changed between deterministic seeds")
        output.append({
            "reading_mode": row["reading_mode"], "condition": row["condition"],
            "world": row["world"], "task_id": row["task_id"], "seed": row["seed"],
            "required_mechanism_id": row["required_mechanism_id"],
            "compiled_claim_count": row["compiled_claim_count"],
            "payload_divergent": row["payload_divergent"],
            "naive_selected_candidate_ids": row["naive_selected_candidate_ids"],
            "majority_selected_candidate_ids": row["majority_selected_candidate_ids"],
            "truthfinder_selected_candidate_ids": row["truthfinder_selected_candidate_ids"],
            "selection_comparison": comparison,
        })
    write_csv("aggregation_consistency_detail.csv", output)
    dedup_counts = Counter(dedup.values())
    summary = [{"unit": "seed_replicated_required_mechanism", "same": counts["same"],
                "different": counts["different"], "none_or_incomplete": counts["none_selected"],
                "not_applicable_lt2_candidates": counts["not_applicable"]},
               {"unit": "seed_deduplicated_required_mechanism", "same": dedup_counts["same"],
                "different": dedup_counts["different"],
                "none_or_incomplete": dedup_counts["none_selected"],
                "not_applicable_lt2_candidates": dedup_counts["not_applicable"]}]
    write_csv("aggregation_consistency_summary.csv", summary)

    pollution = [row for row in read_csv(ROOT / "reports/main/acceptance_exports/pollution_effect.csv")
                 if row["reading_mode"] == "all_public"]
    raw_retrieved = sum(int(row["synthetic_retrieved_chunk_count"]) for row in pollution)
    raw_plan = sum(int(row["synthetic_final_plan_operator_count"]) for row in pollution)
    if (raw_retrieved, raw_plan) != (15268, 28):
        raise RuntimeError("published pollution counts changed")
    # Each aggregate row contains both deterministic seeds. Standard readers share
    # one evidence package; probe_then_rag has a distinct temporal filter.
    retrieved_by_world = defaultdict(dict)
    plan_by_world = defaultdict(dict)
    standard = {"naive_rag", "majority_rag", "truthfinder_rag", "verify_all",
                "arbiter_shell_naive", "arbiter"}
    for row in pollution:
        method, world = row["method"], row["world"]
        retrieval_family = (
            "public_evidence_union" if method in standard or method == "probe_then_rag" else method
        )
        plan_family = "standard_public_reader" if method in standard else method
        retrieved_by_world[world][retrieval_family] = max(
            retrieved_by_world[world].get(retrieval_family, 0),
            int(row["synthetic_retrieved_chunk_count"]),
        )
        plan_by_world[world][plan_family] = max(
            plan_by_world[world].get(plan_family, 0),
            int(row["synthetic_final_plan_operator_count"]),
        )
    method_dedup_retrieved = sum(sum(value.values()) for value in retrieved_by_world.values())
    method_dedup_plan = sum(sum(value.values()) for value in plan_by_world.values())
    provenance = [
        {"published_count": 330, "meaning": "aggregation selections identical",
         "raw_seed_replicated": counts["same"], "seed_deduplicated": dedup_counts["same"],
         "method_deduplicated": counts["same"], "seed_and_method_deduplicated": dedup_counts["same"],
         "method_rule": "not method-replicated; three selectors are columns of one comparison"},
        {"published_count": 15268, "meaning": "synthetic retrieved chunk incidences",
         "raw_seed_replicated": raw_retrieved, "seed_deduplicated": raw_retrieved // 2,
         "method_deduplicated": method_dedup_retrieved,
         "seed_and_method_deduplicated": method_dedup_retrieved // 2,
         "method_rule": "union chunks across methods; temporal-filter chunks are a subset"},
        {"published_count": 28, "meaning": "synthetic final-plan operator incidences",
         "raw_seed_replicated": raw_plan, "seed_deduplicated": raw_plan // 2,
         "method_deduplicated": method_dedup_plan,
         "seed_and_method_deduplicated": method_dedup_plan // 2,
         "method_rule": "deduplicate exact world/task/seed/operator identity"},
    ]
    write_csv("appendix_count_provenance.csv", provenance)
    return {"aggregation": summary, "provenance": provenance}


def oracle_diagnostics(records: list[dict[str, Any]]) -> dict[str, Any]:
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in records:
        grouped[row["method"]].append(row)
    summary = []
    for method, rows in sorted(grouped.items()):
        feasible = [row for row in rows if row["true_feasible"]]
        false_abstentions = sum(row["true_feasible"] and row["reported_feasible"] is False
                               for row in rows)
        summary.append({
            "method": method, "episodes": len(rows), "SR": mean(row["metrics"]["SR"] for row in rows),
            "feasible_episodes": len(feasible),
            "feasible_task_SR": mean(row["metrics"]["SR"] for row in feasible),
            "false_abstentions": false_abstentions,
            "overconservatism_rate": false_abstentions / len(feasible),
            "HTR_commit": mean(row["metrics"]["HTR_commit"] for row in rows),
            "HTR_all": mean(row["metrics"]["HTR_all"] for row in rows),
            "probe_count_mean": mean(row["metrics"]["probe_count"] for row in rows),
        })
    write_csv("oracle_reading_diagnostics.csv", summary)
    index = {row["method"]: row for row in summary}
    keys = lambda method: {(row["condition"], row["world"], row["task_id"], row["metrics"]["SR"])
                           for row in grouped[method]}
    used = lambda method: {(row["condition"], row["world"], row["task_id"],
                            tuple(row["used_mechanism_ids"])) for row in grouped[method]}
    comparisons = [{
        "comparison": "verify_all_vs_naive", "same_SR_all_episodes": int(keys("verify_all") == keys("naive_rag")),
        "same_used_mechanisms_all_episodes": int(used("verify_all") == used("naive_rag")),
        "left_SR": index["verify_all"]["SR"], "right_SR": index["naive_rag"]["SR"],
    }]
    base = "naive_rag"
    for method in ("majority_rag", "truthfinder_rag"):
        comparisons.append({
            "comparison": f"{method}_vs_{base}", "same_SR_all_episodes": int(keys(method) == keys(base)),
            "same_used_mechanisms_all_episodes": int(used(method) == used(base)),
            "left_SR": index[method]["SR"], "right_SR": index[base]["SR"],
        })
    write_csv("oracle_reading_method_comparisons.csv", comparisons)
    return {"rows": summary, "comparisons": comparisons}


def report_markdown(results: dict[str, Any]) -> None:
    adam = results["external"]["adam"]
    closure = json.loads((ROOT / "reports/main/post_review/oracle_reading_summary.json").read_text())
    lines = [
        "# Post-review deterministic measurement v2", "",
        "All outputs are offline replays or deterministic oracle-reading runs. Frozen method code and parameters were not changed.", "",
        "## Measurement decisions", "",
        "- Pipeline HTR_commit uses saved execution provenance: validation-routine actions are probes; selected-plan actions are commitments.",
        "- External HTR_commit uses the saved self-reported intent. The separate consistency audit counts a probe-labelled claim later used by a commit action.",
        "- The retired first-use classifier is retained as `degenerate_first_use_HTR_commit`; it is identically zero by construction.",
        "- Submitting infeasible without executing a stale semantic action is not HTR_commit.", "",
        "## ADAM probe audit", "",
        f"Legacy labels count {adam['legacy_probe_count']} probes, including {adam['free_probe_count']} zero-cost calls. "
        f"The corrected world-action count is {adam['corrected_probe_count']} and corrected cost is {adam['corrected_probe_cost']}.", "",
        "## Oracle reading", "",
        f"Episodes: {closure['episodes']}; supplied/required mechanisms: {closure['closure_supplied']}/{closure['closure_required']}; "
        f"compiled/required: {closure['closure_compiled']}/{closure['closure_required']}; LLM calls: {closure['model_calls']}.", "",
        "## Scope", "",
        "External-agent IAA is action-replay only because those logs do not persist a full belief table; unobserved queries are reported as unknown. "
        "External agents did not expose `report_version`, so their IC status is unavailable rather than imputed.",
    ]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    verify_webcraft_pin(ROOT / "config/webcraft_pin.json", WEBCRAFT, require_ready=True)
    if str(WEBCRAFT) not in sys.path:
        sys.path.insert(0, str(WEBCRAFT))
    normalizer, _ = _compiler(WEBCRAFT)
    conflicts = load_conflicts(WEBCRAFT / "data/conflicts.jsonl")
    vectors = {row["id"]: row["truth_vector"] for row in conflicts}
    nodes = load_resource_nodes(WEBCRAFT / "harness/config/arena.json")
    datasets = pipeline_datasets()
    external = external_datasets()
    results = {
        "plan_source": plan_source_reports(datasets, vectors),
        "external": external_reports(external, conflicts, nodes),
        "iaa": iaa_reports(datasets, external, conflicts, normalizer),
        "ic": ic_reports(datasets, external),
        "terminal": terminal_reports(datasets),
        "appendix": aggregation_and_count_provenance(),
        "oracle": oracle_diagnostics(datasets["oracle_reading"]),
    }
    report_markdown(results)
    (OUT / "summary.json").write_text(json.dumps(results, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": "POST_REVIEW_MEASUREMENT_V2_COMPLETE",
                      "pipeline_episodes": results["plan_source"]["episodes"],
                      "oracle_episodes": len(datasets["oracle_reading"]),
                      "external_episodes": sum(map(len, external.values()))}, sort_keys=True))


if __name__ == "__main__":
    main()
