"""Preregistered A6 zero-call review-v13 audit over saved public logs."""
from __future__ import annotations

import csv
import json
import random
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any

import numpy as np

from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts
from scripts.run_measurement_v3_20261014 import ROOT, WEBCRAFT, csv_rows, external_sets, group, jsonl, pipeline_datasets, write_csv
from scripts.run_measurement_v3d_20261015 import (
    EXTERNAL, PIPELINE, common_keys, contact_episode_rows, pipeline_records_common,
    scalar_bootstrap, selected_external_records, v3d_rows,
)


OUT = ROOT / "reports/main/post_review/review_v13_audit"
M3 = ROOT / "reports/main/post_review/measurement_v3"
M3D = ROOT / "reports/main/post_review/measurement_v3d"
PREREG = ROOT / "prereg/review_v13_audit_20261015.md"
SEED = 20261015


def semantic(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [event for event in events if event.get("method") in ACTION_METHODS]


def raw_events(record: dict[str, Any], pipeline: bool) -> list[dict[str, Any]]:
    return record["events"] if pipeline else jsonl(ROOT / record["action_log"])


def terminal(record: dict[str, Any]) -> str:
    score = record.get("gold_score") or record.get("metrics") or {}
    if score.get("SR") or record.get("success"):
        return "solved_or_correct_infeasible"
    if record.get("reported_feasible") is False:
        return "reported_infeasible"
    return str(record.get("terminal", "unknown"))


def contact_type(action_method: str, discriminative: bool) -> str:
    if not discriminative:
        return "none"
    return "query" if action_method == "check_name" else "execution"


def method_records(common: set[tuple[str, str, str]]) -> tuple[dict[tuple[Any, ...], dict[str, Any]], dict[tuple[Any, ...], dict[str, Any]]]:
    external = {key: row for key, row in selected_external_records().items()
                if key[0] == "first_run" and (key[2], key[3], key[4]) in common}
    pipeline = {key: row for key, row in pipeline_records_common().items()
                if (key[2], key[3], key[4]) in common}
    return external, pipeline


def record_key(row: dict[str, Any], family: str) -> tuple[Any, ...]:
    if family == "external":
        return ("first_run", row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))
    return ("all_public", row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))


def contact_definition() -> str:
    return """# Contact operationalization\n\nA stale-specific action is operationalized by A3 `discriminative=1`: intrinsic matching rejects every target-world-valid assertion/action candidate for the same normalized subject and family; a shared drop `mine` requires a later stale predicted-product reference. Query contact is `check_name`; execution contact is `mine`, `craft_grid`, or `smelt`.\n\n## Difference from literal plan-existence wording\n\nA3 compares candidate assertions that license the action rather than enumerating every complete legal plan. Saved logs contain no complete target-world plan enumeration, so extensional equivalence cannot be independently proven here. The action-level candidate comparison is the available observable proxy; this audit does not infer absence of an unlogged alternative plan. No further implementation difference was found in the saved action rows.\n"""


def contact_split(records_ext: dict[tuple[Any, ...], dict[str, Any]], records_pipe: dict[tuple[Any, ...], dict[str, Any]]) -> list[dict[str, Any]]:
    rows = csv_rows(M3D / "discriminative_contact_v3d_episodes.csv")
    output = []
    for family, records in (("external", records_ext), ("pipeline", records_pipe)):
        for method in (EXTERNAL if family == "external" else PIPELINE):
            values = [row for row in rows if row["agent_family"] == family and row["method"] == method]
            if len(values) != 110:
                raise RuntimeError(f"contact universe changed: {family}/{method}/{len(values)}")
            feasibility = []
            for row in values:
                record = records[record_key(row, family)]
                feasibility.append(bool(record["true_feasible"]))
            for kind, count_field, episode_field in (("query", "inquiry_action_count", "inquiry_contact"), ("execution", "action_action_count", "action_contact")):
                present = [int(row[episode_field]) for row in values]
                actions = [int(row[count_field]) for row in values]
                feasible_present = [value for value, feasible in zip(present, feasibility) if feasible]
                overlap = sum(int(row["inquiry_contact"]) and int(row["action_contact"]) for row in values)
                output.append({"agent_family": family, "method": method, "contact_type": kind,
                               "episodes_denominator": 110, "feasible_denominator": sum(feasibility),
                               "contact_episodes": sum(present), "contact_actions": sum(actions),
                               "feasible_contact_episodes": sum(feasible_present), "query_execution_overlap_episodes": overlap,
                               "episode_rate": sum(present) / 110, "feasible_episode_rate": sum(feasible_present) / sum(feasibility)})
    naive_rows = [row for row in rows if row["agent_family"] == "pipeline" and row["method"] == "naive_rag"]
    for row in output:
        values = [x for x in rows if x["agent_family"] == row["agent_family"] and x["method"] == row["method"]]
        field = "inquiry_contact" if row["contact_type"] == "query" else "action_contact"
        point, low, high = scalar_bootstrap(values, naive_rows, field) if row["method"] != "naive_rag" else (0.0, 0.0, 0.0)
        row.update({"minus_naive": point, "difference_ci_low": low, "difference_ci_high": high})
    return output


def all_v3d_actions(common: set[tuple[str, str, str]], external: dict[tuple[Any, ...], dict[str, Any]], pipeline: dict[tuple[Any, ...], dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    external_rows = [row for row in csv_rows(M3D / "propagation_v3d_actions.csv")
                     if row["setting"] == "first_run" and (row["condition"], row["world"], row["task_id"]) in common]
    pipeline_rows = [row for row in csv_rows(M3 / "pipeline_actions_v3.csv")
                     if row["reading_mode"] == "all_public" and int(row["seed"]) == 20261006 and row["method"] in PIPELINE
                     and (row["condition"], row["world"], row["task_id"]) in common]
    pipe_v3d = v3d_rows([{**row, "setting": "all_public"} for row in pipeline_rows], pipeline, pipeline=True)
    return external_rows, pipe_v3d


def episode_flags(actions: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], family: str) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    indexed: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in actions:
        indexed[(row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))].append(row)
    episodes, mechanisms = [], []
    for key, record in records.items():
        _, method, condition, world, task, seed = key
        rows = indexed.get((method, condition, world, task, seed), [])
        specific = [row for row in rows if int(row.get("world_denied_discriminative", 0))]
        any_denied = [row for row in rows if int(row.get("mismatch", 0))]
        lower = int(any(int(row.get("v3d_propagation", 0)) for row in specific))
        upper = int(lower or any(row.get("v3_label") == "undetermined" for row in specific))
        episodes.append({"agent_family": family, "method": method, "condition": condition, "world": world, "task_id": task, "seed": seed,
                         "primary_rebuttal": int(bool(specific)), "secondary_rebuttal": int(bool(any_denied)),
                         "v3d_lower": lower, "v3d_upper": upper})
        for row in specific:
            for mechanism in json.loads(row["mechanism_families"]):
                mechanisms.append({"agent_family": family, "method": method, "mechanism_family": mechanism})
    return episodes, mechanisms


def rebuttal_table(episodes: list[dict[str, Any]], mechanisms: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for (family, method), values in sorted(group(episodes, "agent_family", "method").items()):
        primary = sum(int(row["primary_rebuttal"]) for row in values)
        lower = sum(int(row["v3d_lower"]) for row in values)
        output.append({"agent_family": family, "method": method, "episodes": len(values),
                       "primary_rebuttal_episodes": primary,
                       "secondary_rebuttal_episodes": sum(int(row["secondary_rebuttal"]) for row in values),
                       "v3d_lower_episodes": lower, "v3d_upper_episodes": sum(int(row["v3d_upper"]) for row in values),
                       "overall_propagation_rate": lower / len(values),
                       "conditional_propagation_rate": lower / primary if primary else "not_applicable",
                       "conditional_rate_not_for_ranking": True})
    for row in output:
        counts = Counter(item["mechanism_family"] for item in mechanisms if item["agent_family"] == row["agent_family"] and item["method"] == row["method"])
        row["primary_rebuttal_mechanisms"] = json.dumps(dict(sorted(counts.items())), sort_keys=True)
    return output


def compact_event(event: dict[str, Any]) -> str:
    return json.dumps({"method": event.get("method"), "params": event.get("params"), "success": event.get("success"), "result": event.get("result")}, sort_keys=True, ensure_ascii=True)[:700]


def event_audit(actions: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], family: str) -> tuple[list[dict[str, Any]], int]:
    output, corrected = [], 0
    for row in actions:
        if not int(row.get("v3d_propagation", 0)):
            continue
        record = records[record_key(row, family)]
        events = raw_events(record, family == "pipeline")
        index = int(row["event_index"])
        witnesses = [name for name, field in (("i", "v3d_rule_i_fifo_or_absent"), ("ii", "v3d_rule_ii_discriminative_repeat"), ("iii_prime", "v3d_rule_iii_discriminative_affirmative")) if int(row.get(field, 0))]
        later = min(json.loads(row.get("referenced_at", "[]")) + json.loads(row.get("repeated_at", "[]")) + ([int(row["affirmative_submit_at"])] if row.get("affirmative_submit_at", "") != "" else []), default=None)
        query_corrected = row["action_method"] == "check_name" and int(row.get("mismatch", 0)) and bool(json.loads(row.get("route_change_at", "[]"))) and terminal(record) == "solved_or_correct_infeasible"
        iii_only = witnesses == ["iii_prime"]
        posthoc = int(iii_only and query_corrected)
        corrected += posthoc
        output.append({"agent_family": family, "method": row["method"], "condition": row["condition"], "world": row["world"], "task_id": row["task_id"], "seed": row["seed"],
                       "event_index": index, "trigger_rules": ";".join(witnesses), "query_corrected_route": int(query_corrected), "posthoc_iii_prime_removed": posthoc,
                       "stale_action": compact_event(events[index]), "world_feedback": compact_event(events[index]),
                       "later_action": "" if later is None else compact_event(events[later]), "terminal": terminal(record)})
    return output, corrected


def audit_samples(actions: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], family: str, rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    candidates = {
        "S1_propagation": [row for row in actions if int(row.get("v3d_propagation", 0))],
        "S2_query": [row for row in actions if int(row.get("discriminative", 0)) and row["action_method"] == "check_name"],
        "S2_execution": [row for row in actions if int(row.get("discriminative", 0)) and row["action_method"] != "check_name"],
        "S3_nonspecific": [row for row in actions if int(row.get("mismatch", 0)) and not int(row.get("discriminative", 0))],
    }
    selected = list(candidates["S1_propagation"])
    for name, cap in (("S2_query", 30), ("S2_execution", 30), ("S3_nonspecific", 40)):
        values = list(candidates[name]); rng.shuffle(values); selected.extend(values[:cap])
    seen = set(); selected = [row for row in selected if not (key := (family, row["method"], row["condition"], row["world"], row["task_id"], row["seed"], row["event_index"])) in seen and not seen.add(key)]
    sheet, key_rows = [], []
    for number, row in enumerate(selected, 1):
        record = records[record_key(row, family)]; events = raw_events(record, family == "pipeline"); index = int(row["event_index"])
        snippets = [str(hit.get("text", ""))[:300] for event in events[:index] if event.get("method") == "search" for hit in (event.get("result") or []) if isinstance(hit, dict)][-2:]
        identifier = f"A6-{family[0].upper()}-{number:03d}"
        sheet.append({"trajectory_id": identifier, "agent_family": family, "method": row["method"], "condition": row["condition"], "world": row["world"], "task_id": row["task_id"],
                      "action_sequence_and_feedback": " -> ".join(compact_event(event) for event in events[max(0, index - 1):min(len(events), index + 3)]), "search_snippets": "\n---\n".join(snippets),
                      "contact_type": "", "propagation": "", "terminal_depends_on_stale": "", "notes": ""})
        key_rows.append({"trajectory_id": identifier, "stratum": "propagation" if int(row.get("v3d_propagation", 0)) else ("query" if row["action_method"] == "check_name" and int(row.get("discriminative", 0)) else ("execution" if int(row.get("discriminative", 0)) else "nonspecific")),
                         "contact_type": contact_type(row["action_method"], bool(int(row.get("discriminative", 0)))),
                         "propagation": "yes" if int(row.get("v3d_propagation", 0)) else ("undetermined" if row.get("v3_label") == "undetermined" else "no"),
                         "terminal_depends_on_stale": "undetermined", "notes": "saved-log scorer label"})
    return sheet, key_rows


def abstention_samples(actions: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], family: str, rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    action_keys = {(row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"])) for row in actions}
    candidates = []
    for key, record in records.items():
        _, method, condition, world, task, seed = key
        if (method, condition, world, task, seed) not in action_keys and record.get("reported_feasible") is False:
            candidates.append((key, record))
    rng.shuffle(candidates)
    sheet, key_rows = [], []
    for number, (key, record) in enumerate(candidates[:20], 1):
        events = raw_events(record, family == "pipeline")
        identifier = f"A6-{family[0].upper()}-S4-{number:03d}"
        sheet.append({"trajectory_id": identifier, "agent_family": family, "method": record["method"], "condition": record["condition"], "world": record["world"], "task_id": record["task_id"],
                      "action_sequence_and_feedback": " -> ".join(compact_event(event) for event in events[-4:]), "search_snippets": "", "contact_type": "", "propagation": "", "terminal_depends_on_stale": "", "notes": ""})
        key_rows.append({"trajectory_id": identifier, "stratum": "S4_no_contact_abstention", "contact_type": "none", "propagation": "no", "terminal_depends_on_stale": "no", "notes": "saved direct abstention"})
    return sheet, key_rows


def cap_global_sample(sheet: list[dict[str, Any]], key_rows: list[dict[str, Any]], rng: random.Random) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    caps = {"propagation": None, "query": 30, "execution": 30, "nonspecific": 40, "S4_no_contact_abstention": 20}
    keys = group(key_rows, "stratum")
    keep: set[str] = set()
    for stratum, rows in keys.items():
        values = list(rows); rng.shuffle(values)
        limit = caps[stratum]
        keep.update(row["trajectory_id"] for row in values if limit is None or values.index(row) < limit)
    return [row for row in sheet if row["trajectory_id"] in keep], [row for row in key_rows if row["trajectory_id"] in keep]


def cost_rows() -> list[dict[str, Any]]:
    output = []
    for mode, records in pipeline_datasets().items():
        for record in records:
            if int(record["seed"]) != 20261006:
                continue
            events = record["events"]; acts = semantic(events)
            output.append({"scope": "pipeline_full", "reading_mode": mode, "method": record["method"], "condition": record["condition"], "world": record["world"], "task_id": record["task_id"], "seed": record["seed"],
                           "semantic_actions": len(acts), "semantic_cost": sum(float(event.get("cost", {}).get("primary", 0) or 0) for event in acts), "search_calls": sum(event.get("method") == "search" for event in events), "llm_calls": 0,
                           "probe_label_count": sum(event.get("intent") == "probe" for event in acts)})
    for method, records in group(external_sets()["first_run"], "method").items():
        for record in records:
            events = raw_events(record, False); acts = semantic(events)
            output.append({"scope": "unified_110_external", "reading_mode": "all_public", "method": method, "condition": record["condition"], "world": record["world"], "task_id": record["task_id"], "seed": record["seed"],
                           "semantic_actions": len(acts), "semantic_cost": sum(float(event.get("cost", {}).get("primary", 0) or 0) for event in acts), "search_calls": sum(event.get("method") == "search" for event in events), "llm_calls": int(record.get("model_calls", 0) or 0),
                           "probe_label_count": sum(event.get("intent") == "probe" for event in acts)})
    return output


def cost_pairs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for mode, left, right in (("all_public", "naive_rag", "verify_all"), ("all_public", "parametric", "probe_then_parametric"),
                              ("llm_only", "naive_rag", "verify_all"), ("llm_only", "parametric", "probe_then_parametric"),
                              ("oracle_reading", "naive_rag", "verify_all"), ("oracle_reading", "parametric", "probe_then_parametric")):
        lrows = [r for r in rows if r["scope"] == "pipeline_full" and r["reading_mode"] == mode and r["method"] == left]
        rrows = [r for r in rows if r["scope"] == "pipeline_full" and r["reading_mode"] == mode and r["method"] == right]
        index_l = {(r["condition"], r["world"], r["task_id"]): r for r in lrows}; index_r = {(r["condition"], r["world"], r["task_id"]): r for r in rrows}
        for metric in ("semantic_actions", "semantic_cost", "search_calls", "llm_calls", "probe_label_count"):
            diff = [float(index_r[key][metric]) - float(index_l[key][metric]) for key in index_l]
            output.append({"reading_mode": mode, "left_method": left, "right_method": right, "metric": metric, "instances": len(diff),
                           "right_minus_left": float(np.mean(diff)), "bootstrap_ci_low": float(np.quantile(diff, .025)), "bootstrap_ci_high": float(np.quantile(diff, .975)),
                           "note": "Probe is an existing intent-label count, not an independent cost."})
    return output


def behavior_pairs() -> list[dict[str, Any]]:
    source = [row for row in csv_rows(ROOT / "reports/main/post_review/measurement_v3_followup/behavior_distinguishability.csv") if row["scope"] == "unified_110" and int(row["SR_indistinguishable"])]
    terminal_p = sorted(float(row["terminal_permutation_p"]) for row in source)
    output = []
    for row in source:
        p = float(row["terminal_permutation_p"])
        rank = terminal_p.index(p) + 1
        output.append({**row, "SR_test": "paired_bootstrap", "behavior_rate_test": "paired_bootstrap", "terminal_test": "paired_permutation", "raw_terminal_p": p,
                       "holm_terminal_p": min(1.0, p * (len(source) - rank + 1)), "holm_behavior_separated": int(row["behavior_separated"]) and p * (len(source) - rank + 1) < .05,
                       "always_abstain_included": "always_abstain" in {row["left_method"], row["right_method"]}})
    return output


def iaa_summary() -> list[dict[str, Any]]:
    rows = csv_rows(ROOT / "reports/main/post_review/iaa_query_inventory.csv")
    output = []
    for (mode, method), values in sorted(group(rows, "reading_mode", "method").items()):
        truth = Counter(str(row["truth"]) for row in values); answered = [row for row in values if not int(row["unknown"])]
        baseline = max(truth.values()) / len(values)
        correct = sum(int(row["correct"]) for row in answered)
        accuracy = correct / len(answered) if answered else ""
        n = len(answered); z = 1.96
        if n:
            centre = (accuracy + z * z / (2 * n)) / (1 + z * z / n)
            radius = z * np.sqrt(accuracy * (1 - accuracy) / n + z * z / (4 * n * n)) / (1 + z * z / n)
            units = group(values, "condition", "world", "task_id", "seed")
            counts = [(sum(int(x["correct"]) for x in unit), sum(not int(x["unknown"]) for x in unit)) for unit in units.values()]
            rng = np.random.default_rng(SEED)
            draws = []
            for _ in range(20):
                sampled = rng.integers(0, len(counts), size=(500, len(counts)))
                numer = np.array([counts[i][0] for i in range(len(counts))], dtype=float)[sampled].sum(axis=1)
                denom = np.array([counts[i][1] for i in range(len(counts))], dtype=float)[sampled].sum(axis=1)
                draws.extend((numer[denom > 0] / denom[denom > 0] - baseline).tolist())
            b_low, b_high = (float(x) for x in np.quantile(draws, [.025, .975]))
        else:
            centre = radius = b_low = b_high = ""
        output.append({"reading_mode": mode, "method": method, "queries": len(values), "true_count": truth["True"], "false_count": truth["False"],
                       "majority_baseline": baseline, "coverage": len(answered) / len(values), "conditional_accuracy": accuracy,
                       "conditional_minus_baseline": accuracy - baseline if answered else "", "wilson_accuracy_low": centre - radius if n else "", "wilson_accuracy_high": centre + radius if n else "",
                       "difference_bootstrap_ci_low": b_low, "difference_bootstrap_ci_high": b_high})
    for method in EXTERNAL:
        output.append({"reading_mode": "all_public", "method": method, "queries": "not_available", "true_count": "", "false_count": "", "majority_baseline": "", "coverage": "not_available_saved_external_belief", "conditional_accuracy": "", "conditional_minus_baseline": "", "wilson_accuracy_low": "", "wilson_accuracy_high": "", "difference_bootstrap_ci_low": "", "difference_bootstrap_ci_high": ""})
    return output


def gold_audit() -> list[dict[str, Any]]:
    output = []
    for record in external_sets()["oracle_knowledge"]:
        events = raw_events(record, False); searches = [event for event in events if event.get("method") == "search"]
        returned = [hit for event in searches for hit in (event.get("result") or []) if isinstance(hit, dict)]
        output.append({"method": record["method"], "condition": record["condition"], "world": record["world"], "task_id": record["task_id"], "seed": record["seed"],
                       "saved_knowledge_source": "template_gold_library_setting", "same_library_as_pipeline_oracle_reading": "not_observable_from_external_log",
                       "oracle_path_required_mechanism_coverage": "not_observable_without_hidden_oracle_path", "drop_coverage": "not_observable_without_hidden_oracle_path",
                       "search_returned_rows": len(returned), "first_search_step": next((index for index, event in enumerate(events) if event.get("method") == "search"), ""),
                       "prompt_log_available": False, "returned_text_observably_in_prompt": "not_observable", "truncation_observable": "not_observable",
                       "oracle_cost_within_90_actions_40_calls": int(float(record.get("primary_cost", 0) or 0) <= 90 and int(record.get("model_calls", 0) or 0) <= 40)})
    return output


def main() -> None:
    if not PREREG.is_file(): raise RuntimeError("missing A6 preregistration")
    external_all = selected_external_records(); common = common_keys(external_all)
    external, pipeline = method_records(common)
    ext_actions, pipe_actions = all_v3d_actions(common, external, pipeline)
    contact = contact_split(external, pipeline)
    ext_eps, ext_mech = episode_flags(ext_actions, external, "external")
    pipe_eps, pipe_mech = episode_flags(pipe_actions, pipeline, "pipeline")
    rebuttal = rebuttal_table(ext_eps + pipe_eps, ext_mech + pipe_mech)
    ext_event_rows, ext_removed = event_audit(ext_actions, external, "external")
    pipe_event_rows, pipe_removed = event_audit(pipe_actions, pipeline, "pipeline")
    rng = random.Random(SEED)
    sheet1, key1 = audit_samples(ext_actions, external, "external", rng)
    sheet2, key2 = audit_samples(pipe_actions, pipeline, "pipeline", rng)
    sheet3, key3 = abstention_samples(ext_actions, external, "external", rng)
    sheet, key_rows = cap_global_sample(sheet1 + sheet2 + sheet3, key1 + key2 + key3, rng)
    costs = cost_rows(); gold = gold_audit()
    OUT.mkdir(parents=True, exist_ok=True)
    (OUT / "contact_definition.md").write_text(contact_definition())
    write_csv(OUT / "contact_split.csv", contact)
    write_csv(OUT / "rebuttal_propagation.csv", rebuttal)
    write_csv(OUT / "propagation_event_audit.csv", ext_event_rows + pipe_event_rows or [{"agent_family":"none","method":"","condition":"","world":"","task_id":"","seed":"","event_index":"","trigger_rules":"","query_corrected_route":"","posthoc_iii_prime_removed":"","stale_action":"","world_feedback":"","later_action":"","terminal":""}])
    write_csv(OUT / "audit_sheet.csv", sheet)
    write_csv(OUT / "audit_key.csv", key_rows)
    write_csv(OUT / "real_cost.csv", costs)
    write_csv(OUT / "real_cost_pairs.csv", cost_pairs(costs))
    write_csv(OUT / "gold_control_audit.csv", gold)
    behavior = behavior_pairs()
    write_csv(OUT / "behavior_pairwise.csv", behavior)
    iaa = iaa_summary()
    write_csv(OUT / "iaa_v13.csv", iaa)
    report = ["# Review v13 A6 audit", "", "Zero LLM calls and zero new episodes.", "", f"Post-hoc iii' removals: {ext_removed + pipe_removed}.", f"Audit-sheet rows: {len(sheet)}.", "Gold-control verdict: knowledge sufficiency is not observable from the saved external prompt logs; no stronger claim is made.", ""]
    (OUT / "REPORT.md").write_text("\n".join(report))
    (OUT / "provenance.json").write_text(json.dumps({"status":"REVIEW_V13_A6_COMPLETE","llm_calls":0,"new_episodes":0,"rng_seed":SEED,"rule_formation_time":"2026-10-15T06:22:00Z"}, indent=2) + "\n")
    print(json.dumps({"status":"REVIEW_V13_A6_COMPLETE","llm_calls":0,"audit_rows":len(sheet),"iii_removed":ext_removed+pipe_removed}, sort_keys=True))


if __name__ == "__main__": main()
