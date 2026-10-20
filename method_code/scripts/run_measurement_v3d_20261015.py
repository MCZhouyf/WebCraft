"""Run preregistered A5 v3-d offline, zero-call measurement reanalysis."""
from __future__ import annotations

import csv
import hashlib
import json
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from arbiter.evaluation.claim_compiler import CorpusItemNormalizer
from arbiter.evaluation.consequence_htr import inventory_dependencies
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts
from arbiter.evaluation.measurement_v3 import _inventory_before, event_inputs, item
from arbiter.evaluation.p6_execution import _claim_assertions, _compiler
from scripts.run_measurement_reanalysis_20261013 import evidence_path
from scripts.run_measurement_v3_20261014 import (
    EXTERNAL_PATHS, ROOT, WEBCRAFT, csv_rows, external_sets, group, jsonl,
    pipeline_datasets, write_csv,
)
from scripts.run_measurement_v3_followup_20261015 import (
    DRAWS, RNG_SEED, ratio_ci, ratio_difference_ci,
)


OUT = ROOT / "reports/main/post_review/measurement_v3d"
M3 = ROOT / "reports/main/post_review/measurement_v3"
A4 = ROOT / "reports/main/post_review/measurement_v3_followup"
PREREG = ROOT / "prereg/measurement_v3d_20261015.md"
EXTERNAL = ("direct_llm", "react", "reflexion", "adam", "xenon")
PIPELINE = (
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "oracle",
    "arbiter", "probe_then_rag",
)


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"))


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def as_int(row: dict[str, Any], field: str) -> int:
    return int(row.get(field, 0) or 0)


def selected_external_records() -> dict[tuple[str, str, str, str, str, int], dict[str, Any]]:
    selected = {}
    for setting in ("first_run", "oracle_knowledge"):
        for record in external_sets()[setting]:
            key = (setting, record["method"], record["condition"], record["world"], record["task_id"], int(record["seed"]))
            selected[key] = record
    return selected


def external_action_rows() -> list[dict[str, Any]]:
    return [row for row in csv_rows(M3 / "external_actions_v3.csv")
            if row["setting"] in {"first_run", "oracle_knowledge"}]


def pipeline_records_common() -> dict[tuple[str, str, str, str, str, int], dict[str, Any]]:
    output = {}
    for mode, records in pipeline_datasets().items():
        if mode != "all_public":
            continue
        for record in records:
            if record["method"] not in PIPELINE or int(record["seed"]) != 20261006:
                continue
            output[(mode, record["method"], record["condition"], record["world"], record["task_id"], int(record["seed"]))] = record
    return output


def common_keys(records: dict[tuple[str, str, str, str, str, int], dict[str, Any]]) -> set[tuple[str, str, str]]:
    keys = {(condition, world, task) for (setting, method, condition, world, task, _), _record in records.items()
            if setting == "first_run" and method == "direct_llm"}
    if len(keys) != 110:
        raise RuntimeError(f"expected 110 external common keys, got {len(keys)}")
    return keys


def raw_events(record: dict[str, Any], pipeline: bool) -> list[dict[str, Any]]:
    return record["events"] if pipeline else jsonl(ROOT / record["action_log"])


def action_inputs_absent(events: list[dict[str, Any]], event_index: int, predicted: set[str]) -> bool:
    """A public-snapshot absence witness for the preregistered FIFO exception."""
    inventory = _inventory_before(events, event_index)
    if inventory is None:
        return False
    inputs = event_inputs(events[event_index]) & predicted
    return bool(inputs) and any(inventory[name] <= 0 for name in inputs)


def v3d_rows(
    rows: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], *, pipeline: bool,
) -> list[dict[str, Any]]:
    output = []
    event_cache: dict[tuple[Any, ...], list[dict[str, Any]]] = {}
    for row in rows:
        prefix = ("all_public",) if pipeline else (row["setting"],)
        key = prefix + (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))
        record = records[key]
        events = event_cache.setdefault(key, raw_events(record, pipeline))
        index = int(row["event_index"])
        predicted = set(json.loads(row["predicted_products"]))
        references = [int(value) for value in json.loads(row["referenced_at"])]
        repeats = [int(value) for value in json.loads(row["repeated_at"])]
        deps = inventory_dependencies(events).get(index, [])
        fifo_ref = any(consumer > index and name in predicted for consumer, name, _ in deps)
        absent_ref = any(action_inputs_absent(events, ref, predicted) for ref in references)
        discriminative = as_int(row, "discriminative")
        denied = as_int(row, "mismatch")
        commit = row["v3_label"] == "commit"
        rule_i = int(discriminative and denied and commit and (fifo_ref or absent_ref))
        rule_ii = int(discriminative and denied and commit and bool(repeats))
        rule_iii = int(discriminative and denied and commit and row["affirmative_submit_at"] != "")
        propagation = int(bool(rule_i or rule_ii or rule_iii))
        nondisc_repeat = int(denied and not discriminative and bool(repeats))
        prereq_loop = int(nondisc_repeat and row["prerequisite_ready"] == "false")
        output.append({
            **row, "world_denied_discriminative": int(denied and discriminative),
            "v3d_rule_i_fifo_or_absent": rule_i, "v3d_rule_ii_discriminative_repeat": rule_ii,
            "v3d_rule_iii_discriminative_affirmative": rule_iii,
            "v3d_propagation": propagation, "repeat_nondiscriminative": nondisc_repeat,
            "prerequisite_missing_loop": prereq_loop,
            "fifo_reference": int(fifo_ref), "absent_reference": int(absent_ref),
            "raw_reference_count": len(references), "raw_repeat_count": len(repeats),
        })
    return output


def ratio_by_task(rows: list[dict[str, Any]], numerator: str, denominator: str) -> dict[str, tuple[float, float]]:
    return {
        task: (sum(as_int(row, numerator) for row in values), sum(as_int(row, denominator) for row in values))
        for task, values in group(rows, "task_id").items()
    }


def safe_ratio_difference(
    left: dict[str, tuple[float, float]], right: dict[str, tuple[float, float]], seed_offset: int,
) -> tuple[Any, Any, int]:
    if not left or not right or not any(value[1] for value in left.values()) or not any(value[1] for value in right.values()):
        return "", "", DRAWS
    try:
        return ratio_difference_ci(left, right, seed_offset)
    except IndexError:
        # Every paired resample had a zero conditional denominator.
        return "", "", DRAWS


def episode_v3d(rows: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    indexed: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        indexed[(row["setting"], row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))].append(row)
    for (setting, method, condition, world, task, seed), _record in sorted(records.items()):
        values = indexed.get((setting, method, condition, world, task, seed), [])
        relevant = [row for row in values if as_int(row, "world_denied_discriminative")]
        lower = int(any(as_int(row, "v3d_propagation") for row in relevant))
        upper = int(lower or any(row["v3_label"] == "undetermined" for row in relevant))
        output.append({"setting": setting, "method": method, "condition": condition,
                       "world": world, "task_id": task, "seed": int(seed),
                       "v3d_episode_lower": lower, "v3d_episode_upper": upper})
    return output


def propagation_summary(rows: list[dict[str, Any]], episodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    result, conditions = [], []
    for (setting, method), values in sorted(group(rows, "setting", "method").items()):
        erows = [row for row in episodes if row["setting"] == setting and row["method"] == method]
        denominator = [row for row in values if as_int(row, "world_denied_discriminative")]
        conditional = ratio_by_task(values, "v3d_propagation", "world_denied_discriminative")
        lower = {task: (sum(as_int(row, "v3d_episode_lower") for row in group_rows), len(group_rows))
                 for task, group_rows in group(erows, "task_id").items()}
        upper = {task: (sum(as_int(row, "v3d_episode_upper") for row in group_rows), len(group_rows))
                 for task, group_rows in group(erows, "task_id").items()}
        c_low, c_high, c_omit = ratio_ci(conditional, 101)
        l_low, l_high, l_omit = ratio_ci(lower, 102)
        u_low, u_high, u_omit = ratio_ci(upper, 103)
        result.append({
            "setting": setting, "method": method, "episodes": len(erows),
            "world_denied_discriminative_actions": len(denominator),
            "v3d_propagation_events": sum(as_int(row, "v3d_propagation") for row in denominator),
            "conditional_propagation_rate": (sum(as_int(row, "v3d_propagation") for row in denominator) / len(denominator)) if denominator else "",
            "conditional_ci_low": c_low, "conditional_ci_high": c_high,
            "episode_lower": sum(as_int(row, "v3d_episode_lower") for row in erows) / len(erows),
            "episode_lower_ci_low": l_low, "episode_lower_ci_high": l_high,
            "episode_upper": sum(as_int(row, "v3d_episode_upper") for row in erows) / len(erows),
            "episode_upper_ci_low": u_low, "episode_upper_ci_high": u_high,
            "rule_i_events": sum(as_int(row, "v3d_rule_i_fifo_or_absent") for row in denominator),
            "rule_ii_events": sum(as_int(row, "v3d_rule_ii_discriminative_repeat") for row in denominator),
            "rule_iii_events": sum(as_int(row, "v3d_rule_iii_discriminative_affirmative") for row in denominator),
            "repeat_nondiscriminative_actions": sum(as_int(row, "repeat_nondiscriminative") for row in values),
            "prerequisite_missing_loops": sum(as_int(row, "prerequisite_missing_loop") for row in values),
            "omitted_bootstrap_draws": c_omit + l_omit + u_omit,
        })
        for split, subset in (("matched", [row for row in values if row["condition"].startswith("matched_")]),
                              ("mismatched", [row for row in values if row["condition"].startswith("mismatched_")])):
            counts = ratio_by_task(subset, "v3d_propagation", "world_denied_discriminative")
            numerator, denominator_count = sum(x[0] for x in counts.values()), sum(x[1] for x in counts.values())
            conditions.append({"setting": setting, "method": method, "comparison": split,
                               "numerator": numerator, "denominator": denominator_count,
                               "rate": numerator / denominator_count if denominator_count else ""})
        matched = ratio_by_task([row for row in values if row["condition"].startswith("matched_")], "v3d_propagation", "world_denied_discriminative")
        mismatch = ratio_by_task([row for row in values if row["condition"].startswith("mismatched_")], "v3d_propagation", "world_denied_discriminative")
        low, high, omitted = safe_ratio_difference(mismatch, matched, 201)
        conditions.append({"setting": setting, "method": method, "comparison": "mismatched_minus_matched",
                           "numerator": "", "denominator": "", "rate": "",
                           "difference_ci_low": low, "difference_ci_high": high,
                           "omitted_bootstrap_draws": omitted})
    # Ensure shared CSV fields for rows without an interval.
    fields = ["setting", "method", "comparison", "numerator", "denominator", "rate", "difference_ci_low", "difference_ci_high", "omitted_bootstrap_draws"]
    conditions = [{field: row.get(field, "") for field in fields} for row in conditions]
    return result, conditions


def propagation_vs_naive(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    first = [row for row in rows if row["setting"] == "first_run"]
    naive = ratio_by_task([row for row in first if row["method"] == "direct_llm"], "v3d_propagation", "world_denied_discriminative")
    # The requested comparator is pipeline Naive; unavailable in external action rows would be an error.
    # A joined pipeline comparison is emitted by contact tables, not falsely substituted here.
    return [{"method": method, "comparator": "pipeline_naive_rag", "status": "reported_in_contact_table"}
            for method in EXTERNAL]


def action_contact_rows(rows: list[dict[str, Any]], *, setting: str, family_name: str) -> list[dict[str, Any]]:
    output = []
    for row in rows:
        if not as_int(row, "discriminative"):
            continue
        kind = "inquiry" if row["action_method"] == "check_name" else "action"
        output.append({**row, "analysis_setting": setting, "agent_family": family_name,
                       "contact_total": 1, "contact_inquiry": int(kind == "inquiry"),
                       "contact_action": int(kind == "action")})
    return output


def semantic_count(record: dict[str, Any], pipeline: bool) -> int:
    return sum(event.get("method") in ACTION_METHODS for event in raw_events(record, pipeline))


def contact_episode_rows(
    actions: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], *, pipeline: bool,
) -> list[dict[str, Any]]:
    contacts: dict[tuple[str, str, str, str, int], list[dict[str, Any]]] = defaultdict(list)
    for row in actions:
        contacts[(row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))].append(row)
    output = []
    for record_key, record in records.items():
        prefix = ("all_public",) if pipeline else ("first_run",)
        if record_key[:1] != prefix:
            continue
        _, method, condition, world, task, seed = record_key
        key = (method, condition, world, task, seed)
        values = contacts.get(key, [])
        semantic = semantic_count(record, pipeline)
        output.append({"agent_family": "pipeline" if pipeline else "external", "method": method,
                       "condition": condition, "world": world, "task_id": task, "seed": seed,
                       "semantic_actions": semantic,
                       "total_contact": int(bool(values)),
                       "inquiry_contact": int(any(as_int(row, "contact_inquiry") for row in values)),
                       "action_contact": int(any(as_int(row, "contact_action") for row in values)),
                       "total_action_count": len(values),
                       "inquiry_action_count": sum(as_int(row, "contact_inquiry") for row in values),
                       "action_action_count": sum(as_int(row, "contact_action") for row in values)})
    return output


def scalar_bootstrap(left: list[dict[str, Any]], right: list[dict[str, Any]], field: str) -> tuple[float, float, float]:
    key = lambda r: (r["condition"], r["world"], r["task_id"])
    li, ri = {key(r): float(r[field]) for r in left}, {key(r): float(r[field]) for r in right}
    if set(li) != set(ri) or len(li) != 110:
        raise RuntimeError(f"contact pairing failed for {field}")
    tasks = sorted({value[2] for value in li})
    lv = np.array([sum(li[k] for k in li if k[2] == task) for task in tasks])
    rv = np.array([sum(ri[k] for k in ri if k[2] == task) for task in tasks])
    rng = np.random.default_rng(RNG_SEED)
    draws = []
    for _ in range(20):
        idx = rng.integers(0, len(tasks), size=(500, len(tasks)))
        draws.extend(((lv[idx].sum(axis=1) - rv[idx].sum(axis=1)) / 110).tolist())
    return ((sum(li.values()) - sum(ri.values())) / 110, *map(float, np.quantile(draws, [0.025, 0.975])))


def propagation_effects(episodes: list[dict[str, Any]]) -> list[dict[str, Any]]:
    naive = [row for row in episodes if row["setting"] == "all_public" and row["method"] == "naive_rag"]
    if len(naive) != 110:
        raise RuntimeError("pipeline Naive v3-d comparison is not on the registered 110 keys")
    output = []
    for setting in ("first_run", "oracle_knowledge"):
        methods = EXTERNAL if setting == "first_run" else ("direct_llm", "react")
        for method in methods:
            rows = [row for row in episodes if row["setting"] == setting and row["method"] == method]
            point, low, high = scalar_bootstrap(rows, naive, "v3d_episode_lower")
            output.append({"setting": setting, "method": method, "comparator": "pipeline_naive_rag",
                           "metric": "v3d_episode_lower", "difference": point,
                           "difference_ci_low": low, "difference_ci_high": high})
    return output


def contact_summary(episodes: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    summary, conditions = [], []
    by_family_method = group(episodes, "agent_family", "method")
    naive = [row for row in episodes if row["agent_family"] == "pipeline" and row["method"] == "naive_rag"]
    for (family_name, method), rows in sorted(by_family_method.items()):
        for metric in ("total_contact", "inquiry_contact", "action_contact"):
            point, low, high = scalar_bootstrap(rows, naive, metric) if len(rows) == 110 and method != "naive_rag" else (0.0, 0.0, 0.0)
            event_count = sum(float(row[metric.replace("contact", "action_count")]) for row in rows)
            total_events = sum(float(row["total_action_count"]) for row in rows)
            summary.append({"agent_family": family_name, "method": method, "metric": metric,
                            "episodes": len(rows), "episode_rate": sum(float(row[metric]) for row in rows) / len(rows),
                            "event_count": event_count,
                            "share_of_discriminative_actions": event_count / total_events if total_events else "",
                            "per_100_semantic_actions": 100 * event_count / sum(float(row["semantic_actions"]) for row in rows) if sum(float(row["semantic_actions"]) for row in rows) else "",
                            "minus_pipeline_naive": point, "difference_ci_low": low, "difference_ci_high": high})
            matched = [row for row in rows if row["condition"].startswith("matched_")]
            for name, subset in (("matched", matched), ("mismatched_old2new", [row for row in rows if row["condition"] == "mismatched_old2new"]),
                                 ("mismatched_new2old", [row for row in rows if row["condition"] == "mismatched_new2old"])):
                conditions.append({"agent_family": family_name, "method": method, "metric": metric,
                                   "comparison": name, "episode_rate": sum(float(row[metric]) for row in subset) / len(subset),
                                   "difference_ci_low": "", "difference_ci_high": ""})
            for name, subset in (("mismatched_old2new_minus_matched", [row for row in rows if row["condition"] == "mismatched_old2new"]),
                                 ("mismatched_new2old_minus_matched", [row for row in rows if row["condition"] == "mismatched_new2old"])):
                a = {task: (sum(float(x[metric]) for x in vals), len(vals)) for task, vals in group(subset, "task_id").items()}
                b = {task: (sum(float(x[metric]) for x in vals), len(vals)) for task, vals in group(matched, "task_id").items()}
                low, high, _ = ratio_difference_ci(a, b, 300)
                conditions.append({"agent_family": family_name, "method": method, "metric": metric,
                                   "comparison": name, "episode_rate": sum(v[0] for v in a.values()) / sum(v[1] for v in a.values()),
                                   "difference_ci_low": low, "difference_ci_high": high})
    return summary, conditions


def assertion_key(assertion: dict[str, Any]) -> str:
    return canonical({key: value for key, value in assertion.items() if key != "_execution"})


def provenance_rows(
    actions: list[dict[str, Any]], records: dict[tuple[Any, ...], dict[str, Any]], conflicts: dict[str, dict[str, Any]], *, pipeline: bool,
) -> list[dict[str, Any]]:
    if str(WEBCRAFT) not in sys.path:
        sys.path.insert(0, str(WEBCRAFT))
    from corpus.claims_cache import ClaimsCache
    normalizer, _ = _compiler(WEBCRAFT)
    caches: dict[str, Any] = {}
    output = []
    for row in actions:
        key = (("all_public",) if pipeline else (row["setting"],)) + (row["method"], row["condition"], row["world"], row["task_id"], int(row["seed"]))
        record = records[key]
        events = raw_events(record, pipeline)
        event_index = int(row["event_index"])
        stale_ids = json.loads(row["stale_mechanism_ids"])
        stale_assertions = [conflicts[value]["assertion"] for value in stale_ids]
        search_hits = [hit for event in events[:event_index] if event.get("method") == "search"
                       for hit in (event.get("result") or []) if isinstance(hit, dict)]
        strict = False
        loose = False
        source = "not_detected_corpus_source"
        if pipeline:
            path = evidence_path("all_public", record)
            if path is None:
                source = "parameter_prior_or_no_retrieval"
            elif path.is_file():
                bundle = json.loads(path.read_text())
                search_hits = list(bundle.get("search_rows", []))
        if search_hits:
            cache = caches.setdefault(row["condition"], ClaimsCache(str(WEBCRAFT / "corpus/indexes/formal_p1" / row["condition"])))
            stale_keys = {assertion_key(value) for value in stale_assertions}
            for hit in search_hits:
                text = str(hit.get("text", ""))
                for claim in cache.get(str(hit.get("chunk_id", ""))):
                    for compiled in _claim_assertions(claim, text, normalizer=normalizer):
                        if assertion_key(compiled) in stale_keys:
                            strict = True
                normalized = " ".join(text.lower().replace("minecraft:", "").replace("_", " ").split())
                for assertion in stale_assertions:
                    subject = str(assertion.get("block", assertion.get("output", assertion.get("item", "")))).replace("_", " ").lower()
                    kind = assertion.get("kind")
                    marker = ("drops", "breaking") if kind == "drop" else (("smelting", "furnace") if assertion.get("station") == "furnace" else (("crafting recipe", "recipe") if kind == "recipe" else ("exists", "identifier", "resolvable")))
                    loose = loose or (subject in normalized and any(token in normalized for token in marker))
        output.append({"agent_family": "pipeline" if pipeline else "external", "setting": row.get("setting", "all_public"),
                       "method": row["method"], "condition": row["condition"], "world": row["world"], "task_id": row["task_id"],
                       "seed": row["seed"], "event_index": row["event_index"], "action_method": row["action_method"],
                       "stale_mechanism_ids": row["stale_mechanism_ids"], "strict_source": "corpus_induced" if strict else source,
                       "loose_source": "possibly_corpus_induced" if loose else "not_detected_loose_source",
                       "search_rows_before_action": len(search_hits)})
    return output


def provenance_summary(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    output = []
    for key, values in sorted(group(rows, "agent_family", "setting", "method", "condition").items()):
        strict = Counter(row["strict_source"] for row in values)
        loose = Counter(row["loose_source"] for row in values)
        output.append({"agent_family": key[0], "setting": key[1], "method": key[2], "condition": key[3], "actions": len(values),
                       "strict_corpus_induced": strict["corpus_induced"], "strict_not_detected": strict["not_detected_corpus_source"],
                       "parameter_prior_or_no_retrieval": strict["parameter_prior_or_no_retrieval"],
                       "strict_corpus_induced_rate": strict["corpus_induced"] / len(values),
                       "loose_possibly_induced": loose["possibly_corpus_induced"],
                       "loose_not_detected": loose["not_detected_loose_source"],
                       "loose_possibly_induced_rate": loose["possibly_corpus_induced"] / len(values)})
    return output


def behavior_summary() -> list[dict[str, Any]]:
    rows = [row for row in csv_rows(A4 / "behavior_distinguishability.csv") if row["scope"] == "unified_110"]
    output = []
    for label, values in (("with_always_abstain", rows), ("without_always_abstain", [row for row in rows if "always_abstain" not in {row["left_method"], row["right_method"]}])):
        indist = [row for row in values if int(row["SR_indistinguishable"])]
        separated = [row for row in indist if int(row["behavior_separated"])]
        output.append({"scope": "unified_110", "always_abstain": label, "total_pairs": len(values),
                       "SR_indistinguishable_pairs": len(indist), "behavior_separated_pairs": len(separated),
                       "unique_separated_pairs": ";".join(f"{r['left_method']}--{r['right_method']}" for r in separated)})
    return output


def report_text(prop: list[dict[str, Any]], contact: list[dict[str, Any]], provenance: list[dict[str, Any]], behavior: list[dict[str, Any]]) -> str:
    first = [row for row in prop if row["setting"] == "first_run"]
    lines = ["# Measurement v3-d (A5)", "", "Zero LLM calls and zero new episodes. All results are supplementary to v3.", "", "## v3-d external five", ""]
    for row in first:
        lines.append(f"- {row['method']}: lower={row['episode_lower']:.4f}, CI=[{row['episode_lower_ci_low']:.4f}, {row['episode_lower_ci_high']:.4f}], conditional={row['conditional_propagation_rate']}.")
    lines.extend(["", "## Attribution caveat", "", "A cache non-match is not evidence of a model prior: public-cache recall is incomplete.", "", "## Behavior-only supplement", ""])
    for row in behavior:
        lines.append(f"- {row['always_abstain']}: {row['SR_indistinguishable_pairs']} SR-indistinguishable pairs; {row['behavior_separated_pairs']} behavior-separated.")
    return "\n".join(lines) + "\n"


def main() -> None:
    if not PREREG.is_file():
        raise RuntimeError("missing A5 preregistration")
    conflicts = {row["id"]: row for row in load_conflicts(WEBCRAFT / "data/conflicts.jsonl")}
    external_records = selected_external_records()
    all_external_actions = external_action_rows()
    common = common_keys(external_records)
    external_actions = [row for row in all_external_actions if (row["condition"], row["world"], row["task_id"]) in common]
    external_v3d = v3d_rows(external_actions, external_records, pipeline=False)
    selected_records = {key: value for key, value in external_records.items()
                        if (key[2], key[3], key[4]) in common}
    external_eps = episode_v3d(external_v3d, selected_records)

    pipeline_records = {key: value for key, value in pipeline_records_common().items()
                        if (key[2], key[3], key[4]) in common}
    pipeline_actions = [row for row in csv_rows(M3 / "pipeline_actions_v3.csv")
                        if row["reading_mode"] == "all_public" and int(row["seed"]) == 20261006
                        and row["method"] in PIPELINE and (row["condition"], row["world"], row["task_id"]) in common]
    pipeline_v3d = v3d_rows([{**row, "setting": "all_public"} for row in pipeline_actions], pipeline_records, pipeline=True)
    pipeline_eps = episode_v3d(pipeline_v3d, pipeline_records)
    prop, prop_conditions = propagation_summary(external_v3d + pipeline_v3d, external_eps + pipeline_eps)
    prop_effects = propagation_effects(external_eps + pipeline_eps)
    e_contacts = action_contact_rows([row for row in external_actions if row["setting"] == "first_run"], setting="first_run", family_name="external")
    p_contacts = action_contact_rows(pipeline_actions, setting="all_public", family_name="pipeline")
    e_contact_eps = contact_episode_rows(e_contacts, selected_records, pipeline=False)
    p_contact_eps = contact_episode_rows(p_contacts, pipeline_records, pipeline=True)
    contact, contact_conditions = contact_summary(e_contact_eps + p_contact_eps)

    provenance = provenance_rows([row for row in external_actions if row["setting"] in {"first_run", "oracle_knowledge"} and as_int(row, "discriminative")], external_records, conflicts, pipeline=False)
    provenance += provenance_rows([row for row in pipeline_actions if as_int(row, "discriminative")], pipeline_records, conflicts, pipeline=True)
    provenance_summary_rows = provenance_summary(provenance)
    behavior = behavior_summary()

    OUT.mkdir(parents=True, exist_ok=True)
    write_csv(OUT / "propagation_v3d.csv", prop)
    write_csv(OUT / "propagation_v3d_actions.csv", external_v3d)
    write_csv(OUT / "propagation_v3d_conditions.csv", prop_conditions)
    write_csv(OUT / "propagation_v3d_effects.csv", prop_effects)
    write_csv(OUT / "discriminative_contact_v3d.csv", contact)
    write_csv(OUT / "discriminative_contact_v3d_conditions.csv", contact_conditions)
    write_csv(OUT / "discriminative_contact_v3d_episodes.csv", e_contact_eps + p_contact_eps)
    write_csv(OUT / "stale_action_provenance.csv", provenance)
    write_csv(OUT / "stale_action_provenance_summary.csv", provenance_summary_rows)
    write_csv(OUT / "behavior_distinguishability_v3d.csv", behavior)
    (OUT / "REPORT.md").write_text(report_text(prop, contact, provenance_summary_rows, behavior))
    provenance_json = {"status": "MEASUREMENT_V3D_COMPLETE", "llm_calls": 0, "new_episodes": 0,
                       "bootstrap_seed": RNG_SEED, "bootstrap_draws": DRAWS, "preregistration_sha256": sha256(PREREG),
                       "inputs": {str(M3.relative_to(ROOT)): "existing A3 exports", str(A4.relative_to(ROOT)): "existing A4 exports"},
                       "external_actions": len(external_actions), "pipeline_actions": len(pipeline_actions)}
    (OUT / "provenance.json").write_text(json.dumps(provenance_json, indent=2, sort_keys=True) + "\n")
    print(json.dumps({"status": provenance_json["status"], "output": str(OUT), "llm_calls": 0}, sort_keys=True))


if __name__ == "__main__":
    main()
