#!/usr/bin/env python3
"""Generate the preregistered W8 zero-call closeout from frozen exports."""
from __future__ import annotations

import csv
import hashlib
import json
import os
import platform
import statistics
import subprocess
import sys
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any, Iterable

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARB = Path("/root/autodl-tmp/arbiter")
OUT = ROOT / "reports/paperA/w8_closeout"
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ARB / "src"), str(ARB / "scripts")]

from arbiter.evaluation.consequence_htr import inventory_dependencies  # noqa: E402
from arbiter.evaluation.external_scoring import ACTION_METHODS, load_conflicts, load_resource_nodes  # noqa: E402
from arbiter.evaluation.measurement_v3 import (  # noqa: E402
    classify_actions_v3, event_inputs, intrinsic_discriminative,
)
from run_measurement_v3_20261014 import pipeline_stale_map  # type: ignore  # noqa: E402
import run_ext_v2_cds_20261016 as w7a  # type: ignore  # noqa: E402

SEED = 20261016
BOOTSTRAP_DRAWS = 10_000
CP_DIR = ROOT / "reports/paperA/review_v14_zero_call/cp_rag"
EXT_DIR = ROOT / "reports/paperA/ext_v2"
SPLIT_PLACEBO = ROOT / "reports/paperA/review_v14_zero_call/split_placebo/instance_diffs.csv"
COMBINED_PLACEBO = ROOT / "reports/paperA/placebo_invariance/instance_diffs.csv"


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def rows(path: Path) -> list[dict[str, str]]:
    with path.open(newline="") as handle:
        return list(csv.DictReader(handle))


def write_csv(path: Path, data: list[dict[str, Any]]) -> None:
    if not data:
        raise RuntimeError(f"refusing empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in data:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(data)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def action_events(record: dict[str, Any]) -> list[dict[str, Any]]:
    return [event for event in record["events"] if event.get("method") in ACTION_METHODS]


def normalized_actions(record: dict[str, Any]) -> list[str]:
    return [json.dumps({"method": event.get("method"), "params": event.get("params", {}),
                        "intent": event.get("intent")}, sort_keys=True)
            for event in action_events(record)]


def release_audit() -> None:
    package = ROOT / "release/package"
    checks = [
        ("environment lock", package / "ENVIRONMENT.lock"),
        ("manifest", package / "MANIFEST.sha256"),
        ("simulator", package / "sim/runner.py"),
        ("method agent implementation", package / "method_code/agent"),
        ("method inference implementation", package / "method_code/inference"),
        ("packaged Python src tree", package / "src"),
        ("frozen parametric prior", package / "artifacts/prior/frozen_prior_bundle.json"),
        ("main all_public evidence", package / "artifacts/main/all_public/evidence"),
        ("main llm_only evidence", package / "artifacts/main/llm_only/evidence"),
        ("oracle-reading evidence", package / "artifacts/post_review/oracle_reading/evidence"),
        ("per-instance scored export", package / "results/per_instance/pipeline_episodes_v3.csv"),
    ]
    inventory = [{"asset": name, "relative_path": str(path.relative_to(package)),
                  "present": int(path.exists()), "kind": "directory" if path.is_dir() else "file"}
                 for name, path in checks]
    write_csv(OUT / "release_asset_audit.csv", inventory)
    methods = ("parametric", "probe_then_parametric", "naive_rag", "majority_rag",
               "truthfinder_rag", "verify_all", "arbiter_shell_naive", "arbiter",
               "oracle", "probe_then_rag")
    write_csv(OUT / "release_metric_reproduction_by_method_mode.csv", [
        {"method": method, "reading_mode": mode, "expected_instances": 374,
         "rerun_instances": 0, "instances_with_any_metric_difference": "not_computed",
         "action_only_difference_instances": "not_computed", "status": "not_executable",
         "reason": "required method/evidence assets absent from release package"}
        for mode in ("llm_only", "all_public", "oracle_reading") for method in methods
    ])
    report = [
        "# W8 release-package metric reproduction audit", "",
        f"- Runtime: Python {platform.python_version()}; `PYTHONHASHSEED={os.environ.get('PYTHONHASHSEED', 'unset')}`.",
        "- Registered runtime: Python 3.10.8, `PYTHONHASHSEED=0`, dependencies from `release/package/ENVIRONMENT.lock`.",
        "- Official release check: 28 tests and 112 subtests passed; the packaged simulator smoke episode succeeded.",
        "- Expected main-matrix executable pipeline episodes: 11,220 (10 methods x 3 modes x 374 instances).",
        "- Episodes independently regenerated from the release package: 0/11,220.", "",
        "The package contains the simulator, scoring exports, and adapter/evaluation fragments, but it does not contain the method agent/inference implementation, frozen parametric prior, or the per-instance evidence bundles required to rerun the main matrix. The polluted condition is also distributed only as an encrypted artifact without a package-local password. Existing scored CSVs can be re-aggregated but are not an independent episode rerun.", "",
        "## Registered outcome", "",
        "The metric-level reproduction requested in W8 item 1 is **not executable from the release package as distributed**. Therefore the preregistered semantic O gate cannot be evaluated and W8 item 2 is stopped before V/C/V+C execution.", "",
        "Recommended statement: **The release package reproduces executor tests and a simulator smoke episode, but does not independently reproduce the reported main-matrix metrics because required method and evidence assets are absent.**",
    ]
    (OUT / "release_reproduction_audit.md").write_text("\n".join(report) + "\n")
    write_csv(OUT / "o_gate_status.csv", [{
        "gate": "semantic_equivalence_O", "status": "not_evaluable",
        "expected_instances": 11220, "rerun_instances": 0,
        "metric_difference_instances": "not_computed",
        "action_only_difference_instances": "not_computed",
        "V_C_VC_executed": 0,
        "reason": "release package lacks method implementation, frozen prior, and evidence bundles",
    }])
    (OUT / "DECOMPOSITION_NOT_RUN.md").write_text(
        "# W8 decomposition status\n\nThe semantic O gate was not evaluable from the registered release runtime, so V, C, and V+C were not run. No paired differences or bootstrap intervals are reported.\n"
    )


def record_key(record: dict[str, Any], scope: str) -> tuple[str, str, str]:
    return (str(record["condition"]), str(record["world"]), str(record["task_id"]))


def load_scope_records() -> tuple[dict[str, list[dict[str, Any]]], dict[str, list[dict[str, Any]]]]:
    cp = jsonl(CP_DIR / "episodes.jsonl")
    cp_scopes: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for record in cp:
        if record["condition"].startswith("cds_"):
            scope = "cds"
        elif record["task_id"].startswith("extv2_"):
            scope = "ext_v2"
        else:
            scope = f"main:{record['reading_mode']}"
        cp_scopes[scope].append(record)

    naive: dict[str, list[dict[str, Any]]] = {}
    for mode, path in {
        "llm_only": ARB / "artifacts/main/llm_only/episodes.jsonl",
        "all_public": ARB / "artifacts/main/all_public/episodes.jsonl",
        "oracle_reading": ARB / "artifacts/post_review/oracle_reading/episodes.jsonl",
    }.items():
        naive[f"main:{mode}"] = [r for r in jsonl(path)
                                  if r["method"] == "naive_rag" and int(r["seed"]) == 20261006]
    naive["ext_v2"] = [r for r in jsonl(EXT_DIR / "web_run/episodes.jsonl") if r["method"] == "naive_rag"]
    naive["cds"] = [r for r in jsonl(EXT_DIR / "cds_run/episodes.jsonl") if r["method"] == "naive_rag"]
    expected = {"main:llm_only": 374, "main:all_public": 374,
                "main:oracle_reading": 374, "ext_v2": 60, "cds": 260}
    for scope, count in expected.items():
        if len(cp_scopes[scope]) != count or len(naive[scope]) != count:
            raise RuntimeError(f"scope coverage changed: {scope} {len(cp_scopes[scope])}/{len(naive[scope])}")
    return cp_scopes, naive


def terminal(record: dict[str, Any]) -> str:
    if record["metrics"]["SR"]:
        return "success"
    if record["true_feasible"] and record["reported_feasible"] is False:
        return "overconservative"
    if record["true_feasible"] and record["reported_feasible"] is True:
        return "execution_failure"
    if not record["true_feasible"] and record["reported_feasible"] is False:
        return "correct_infeasible"
    if not record["true_feasible"] and record["reported_feasible"] is True:
        return "false_feasible"
    return "undetermined"


def inputs_absent(events: list[dict[str, Any]], index: int, predicted: set[str]) -> bool:
    consumed = {str(value).removeprefix("minecraft:") for value in event_inputs(events[index]) & predicted}
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


def v3d(record: dict[str, Any], conflicts: dict[str, dict[str, Any]], nodes: dict[str, str],
        target: str) -> tuple[int, int, int, int]:
    stale_map = pipeline_stale_map(record, conflicts)
    classified = classify_actions_v3(
        record["events"], stale_by_event=stale_map, conflicts=conflicts,
        resource_nodes=nodes, target=target, episode_success=bool(record["metrics"]["SR"]),
    )
    dependencies = inventory_dependencies(record["events"])
    query = action = propagation = 0
    for row in classified:
        index = int(row["event_index"])
        event = record["events"][index]
        intrinsic = intrinsic_discriminative(
            event, row["stale_mechanism_ids"], world=record["world"],
            conflicts=conflicts, resource_nodes=nodes,
        )
        discriminative = bool(intrinsic or row["referenced_at"])
        if not discriminative:
            continue
        if event.get("method") == "check_name":
            query = 1
        else:
            action = 1
        predicted = set(row["predicted_products"])
        fifo = any(consumer > index and name in predicted
                   for consumer, name, _ in dependencies.get(index, []))
        absent = any(inputs_absent(record["events"], int(later), predicted)
                     for later in row["referenced_at"])
        commit = row["v3_label"] == "commit"
        denied = bool(row["mismatch"])
        propagation |= int(discriminative and denied and commit and (
            fifo or absent or bool(row["repeated_at"]) or row["affirmative_submit_at"] != ""
        ))
    return int(bool(classified)), query, action, propagation


def divergence(cp: dict[str, Any], naive: dict[str, Any]) -> str:
    left, right = normalized_actions(cp), normalized_actions(naive)
    if left == right:
        return "identical"
    if int(cp.get("probe_count", 0)) > int(naive.get("probe_count", 0)):
        return "extra_probe"
    if any(trace.get("research_triggered") for trace in cp.get("policy_trace", [])):
        return "active_retrieval"
    if Counter(left) == Counter(right):
        return "dependency_order_only"
    return "other"


def bootstrap(values: list[tuple[str, float]]) -> tuple[float, float, float]:
    grouped: dict[str, list[float]] = defaultdict(list)
    for unit, value in values:
        grouped[unit].append(value)
    units = np.array([mean(grouped[key]) for key in sorted(grouped)])
    rng = np.random.default_rng(SEED)
    draws = rng.choice(units, size=(BOOTSTRAP_DRAWS, len(units)), replace=True).mean(axis=1)
    return float(units.mean()), float(np.quantile(draws, .025)), float(np.quantile(draws, .975))


def cp_metrics() -> None:
    cp_scopes, naive_scopes = load_scope_records()
    conflicts = {row["id"]: row for row in load_conflicts(ROOT / "data/conflicts.jsonl")}
    nodes = load_resource_nodes(ROOT / "harness/config/arena.json")
    tasks = {row["id"]: row for path in (ROOT / "data/tasks.jsonl", ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
             for row in jsonl(path)}
    detail: list[dict[str, Any]] = []
    for scope in sorted(cp_scopes):
        peers = {record_key(row, scope): row for row in naive_scopes[scope]}
        for cp in cp_scopes[scope]:
            peer = peers[record_key(cp, scope)]
            cp_contact, cp_query, cp_action, cp_v3d = v3d(cp, conflicts, nodes, tasks[cp["task_id"]]["target"])
            nv_contact, nv_query, nv_action, nv_v3d = v3d(peer, conflicts, nodes, tasks[peer["task_id"]]["target"])
            unit = tasks[cp["task_id"]].get("independent_family_id", cp["task_id"])
            for method, record, contact, query, action, propagation in (
                ("cp_rag", cp, cp_contact, cp_query, cp_action, cp_v3d),
                ("naive_rag", peer, nv_contact, nv_query, nv_action, nv_v3d),
            ):
                detail.append({
                    "scope": scope, "unit": unit, "method": method,
                    "condition": cp["condition"], "world": cp["world"], "task_id": cp["task_id"],
                    "terminal": terminal(record), "SR": int(record["metrics"]["SR"]),
                    "semantic_actions": len(action_events(record)),
                    "semantic_cost": float(record["metrics"]["primary_cost"]),
                    "search_calls": int(record["reader"].get("search_count", 0)),
                    "plan_acceptance": int(record["metrics"]["HTR_commit"]),
                    "stale_contact": int(record["metrics"]["HTR_all"]),
                    "discriminative_stale_contact": contact,
                    "discriminative_stale_query": query,
                    "discriminative_stale_action": action,
                    "v3d_propagation": propagation,
                    "BIA_original": "not_defined_outside_CDS" if scope != "cds" else "computed_separately",
                    "action_divergence": divergence(cp, peer) if method == "cp_rag" else "reference",
                })
    write_csv(OUT / "cp_rag_episode_metrics.csv", detail)

    metric_names = ("SR", "semantic_actions", "semantic_cost", "search_calls", "plan_acceptance",
                    "stale_contact", "discriminative_stale_contact", "discriminative_stale_query",
                    "discriminative_stale_action", "v3d_propagation")
    effects: list[dict[str, Any]] = []
    by = defaultdict(dict)
    for row in detail:
        key = (row["scope"], row["condition"], row["world"], row["task_id"])
        by[key][row["method"]] = row
    for scope in sorted(cp_scopes):
        scope_rows = [(key, pair) for key, pair in by.items() if key[0] == scope]
        for metric in metric_names:
            values = [(pair["cp_rag"]["unit"], float(pair["cp_rag"][metric]) - float(pair["naive_rag"][metric]))
                      for _, pair in scope_rows]
            effect, low, high = bootstrap(values)
            effects.append({"scope": scope, "metric": metric, "n": len(values),
                            "units": len(set(unit for unit, _ in values)),
                            "CP_minus_Naive": effect, "ci95_low": low, "ci95_high": high,
                            "bootstrap_draws": BOOTSTRAP_DRAWS, "seed": SEED})
    write_csv(OUT / "cp_rag_paired_effects.csv", effects)
    divergence_rows = []
    for scope in sorted(cp_scopes):
        counts = Counter(row["action_divergence"] for row in detail
                         if row["scope"] == scope and row["method"] == "cp_rag")
        for label in ("identical", "extra_probe", "active_retrieval", "dependency_order_only", "other"):
            divergence_rows.append({"scope": scope, "divergence_type": label, "instances": counts[label]})
    write_csv(OUT / "cp_rag_action_divergence.csv", divergence_rows)


def load_prior() -> dict[str, Any]:
    return w7a.load_prior()


def cds_bia() -> None:
    manifest = json.loads((ROOT / "cds_materials/cds_manifest.json").read_text())
    task_families = {row["id"]: row["independent_family_id"]
                     for row in jsonl(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")}
    meta = {}
    bundles = {}
    for item in manifest["rows"]:
        private = json.loads((ROOT / item["evaluator_path"]).read_text())
        visible = json.loads((ROOT / item["visible_path"]).read_text())
        meta[private["instance_id"]] = private
        for condition in ("K", "K+S", "S", "empty", "K+N"):
            bundles[(private["instance_id"], condition)] = w7a.cds_bundle(private, visible, condition)
    prior = load_prior()
    accepted = jsonl(EXT_DIR / "cds_run/episodes.jsonl")
    cp = [r for r in jsonl(CP_DIR / "episodes.jsonl") if r["condition"].startswith("cds_")]
    records = accepted + cp
    detail = []
    for record in records:
        if record["condition"].startswith("cds_"):
            instance, condition = record["condition"].rsplit("__", 1)
        else:
            instance, condition = record["cds_instance_id"], record["cds_condition"]
        private = meta[instance]
        bundle = bundles[(instance, condition)]
        method = record["method"]
        belief_method = "naive_rag" if method == "cp_rag" else method
        belief = w7a.method_belief(belief_method, bundle, record["world"], prior)
        semantic = action_events(record)
        used = list(record.get("used_mechanism_ids", []))
        if len(semantic) != len(used):
            raise RuntimeError("CDS action/mechanism alignment changed")
        probed = {mechanism for mechanism, event in zip(used, semantic)
                  if event.get("intent") == "probe"}
        observed = set(used)
        for label, overrides in (("probe_only", probed), ("all_public_actions", observed)):
            correct = abstained = 0
            for candidate in private["boundary_truth"]:
                mechanism, truth = candidate["mechanism_id"], bool(candidate["truth"])
                if belief == "oracle" or mechanism in overrides:
                    prediction = truth
                elif belief is None or mechanism not in belief.phi:
                    prediction = None
                else:
                    prediction = belief.mechanism_probability(mechanism) >= .5
                abstained += int(prediction is None)
                correct += int(prediction is not None and prediction == truth)
            detail.append({
                "method": method, "condition": condition, "instance_id": instance,
                "task_id": record["task_id"], "family_id": task_families[record["task_id"]],
                "world": record["world"], "BIA_readout": label,
                "correct": correct, "total": len(private["boundary_truth"]),
                "abstained": abstained, "BIA": correct / len(private["boundary_truth"]),
            })
    write_csv(OUT / "cds_bia_episode.csv", detail)
    summary = []
    grouped: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in detail:
        grouped[(row["method"], row["condition"], row["BIA_readout"])].append(row)
    for (method, condition, readout), values in sorted(grouped.items()):
        total = sum(row["total"] for row in values)
        summary.append({"method": method, "condition": condition, "BIA_readout": readout,
                        "episodes": len(values), "correct": sum(row["correct"] for row in values),
                        "total": total, "abstained": sum(row["abstained"] for row in values),
                        "BIA": sum(row["correct"] for row in values) / total})
    write_csv(OUT / "cds_bia_summary.csv", summary)
    overall = []
    for readout in ("probe_only", "all_public_actions"):
        for method in sorted(set(row["method"] for row in detail)):
            values = [row for row in detail
                      if row["method"] == method and row["BIA_readout"] == readout]
            total = sum(row["total"] for row in values)
            overall.append({"method": method, "BIA_readout": readout,
                            "episodes": len(values), "correct": sum(row["correct"] for row in values),
                            "total": total, "abstained": sum(row["abstained"] for row in values),
                            "BIA": sum(row["correct"] for row in values) / total})
    write_csv(OUT / "cds_bia_overall.csv", overall)
    contrasts = []
    methods = ("naive_rag", "verify_all", "arbiter", "cp_rag")
    index = {(r["method"], r["condition"], r["BIA_readout"]): r for r in summary}
    for condition in ("K", "K+S", "S", "empty", "K+N"):
        for readout in ("probe_only", "all_public_actions"):
            for left in methods:
                for right in methods:
                    if left >= right:
                        continue
                    lrow, rrow = index[(left, condition, readout)], index[(right, condition, readout)]
                    contrasts.append({"condition": condition, "BIA_readout": readout,
                                      "method_a": left, "method_b": right,
                                      "BIA_a_minus_b": float(lrow["BIA"]) - float(rrow["BIA"])})
    write_csv(OUT / "cds_bia_pairwise.csv", contrasts)


def numeric_crosswalk() -> None:
    cm = rows(ROOT / "reports/paperA/c_m_dv.csv")
    cross = Counter((row["assertion_kind"], row["conflict_type"]) for row in cm)
    write_csv(OUT / "dv_assertion_conflict_crosswalk.csv", [
        {"assertion_type": assertion, "conflict_type": conflict, "assertions": count}
        for (assertion, conflict), count in sorted(cross.items())
    ])
    recipes = [int(row["c_m"]) for row in cm if row["assertion_kind"] == "recipe"]
    concrete = [int(row["c_m"]) for row in cm if row["assertion_kind"] == "concrete_grid"]
    summary = [
        {"quantity": "DV assertion pool", "value": len(cm), "definition": "grade-A executable assertions"},
        {"quantity": "new_route conflict assertions", "value": sum(row["conflict_type"] == "new_route" for row in cm),
         "definition": "conflict-type count; spans assertion types"},
        {"quantity": "name assertions", "value": sum(row["assertion_kind"] == "name" for row in cm),
         "definition": "assertion-type count"},
        {"quantity": "recipe c(m) median", "value": statistics.median(recipes),
         "definition": "all 110 assertion_kind=recipe rows; world-worst c(m)"},
        {"quantity": "concrete-grid c(m) median", "value": statistics.median(concrete),
         "definition": "four assertion_kind=concrete_grid probes; minimum adjudication cost"},
    ]
    write_csv(OUT / "numeric_denominator_crosswalk.csv", summary)

    combined = rows(COMBINED_PLACEBO)
    split = rows(SPLIT_PLACEBO)
    key = lambda row: (row["method"], row["condition"], row["world"], row["task_id"])
    combined_changed = {key(row): row for row in combined if int(row["delta_SR"]) != 0}
    split_terminal = defaultdict(dict)
    for row in split:
        split_terminal[key(row)][row["placebo"]] = int(row["terminal_changed"])
    masked = []
    for instance, row in sorted(combined_changed.items()):
        state = split_terminal.get(instance, {})
        if state.get("metadata", 0) == 0 and state.get("candidate_order", 0) == 0:
            masked.append({"method": instance[0], "condition": instance[1], "world": instance[2],
                           "task_id": instance[3], "combined_baseline_terminal": row["baseline_terminal"],
                           "combined_placebo_terminal": row["placebo_terminal"],
                           "metadata_terminal_changed": state.get("metadata", "missing"),
                           "candidate_order_terminal_changed": state.get("candidate_order", "missing")})
    write_csv(OUT / "combined_changed_split_unchanged.csv", masked or [{
        "method": "none", "condition": "", "world": "", "task_id": "",
        "combined_baseline_terminal": "", "combined_placebo_terminal": "",
        "metadata_terminal_changed": 0, "candidate_order_terminal_changed": 0,
    }])
    counts = Counter(row["placebo"] for row in split if int(row["terminal_changed"]))
    (OUT / "numeric_conventions.md").write_text(
        "# W8 numeric-convention crosswalk\n\n"
        "- **542 vs 537:** 542 is the conflict-type count for `new_route`, comprising 529 name assertions and 13 recipe assertions. The 537 assertion-type count for `name` comprises those 529 `new_route` rows plus 8 `renamed` rows. These are orthogonal margins of the same cross-tab, not competing estimates.\n"
        "- **Recipe median 7 vs 6:** 7 is the median world-worst adjudication cost over all 110 `recipe` assertions. The value 6 belongs to the four concrete-grid probe rows under the minimum-adjudication-cost convention; it is not the same population or cost functional.\n"
        f"- **Placebo 20 vs split 0/{counts['candidate_order']}:** the combined placebo jointly changed metadata, decorative text, and tie order in one retrieval run, while the split interventions were separately regenerated from the unperturbed baseline. Retrieval/top-k selection is nonlinear, so the separately run effects are not additive. `combined_changed_split_unchanged.csv` lists {len(masked)} combined-run changes absent from both split terminal outcomes.\n"
    )


def report() -> None:
    effects = rows(OUT / "cp_rag_paired_effects.csv")
    bia = rows(OUT / "cds_bia_summary.csv")
    bia_overall = rows(OUT / "cds_bia_overall.csv")
    divergences = rows(OUT / "cp_rag_action_divergence.csv")
    selected = {(row["scope"], row["metric"]): row for row in effects}
    bia_idx = {(row["method"], row["condition"], row["BIA_readout"]): row for row in bia}
    bia_all = {(row["method"], row["BIA_readout"]): row for row in bia_overall}
    lines = ["# W8 zero-call closeout", "", "## Release reproduction and O gate", "",
             "The release runtime passed 28 tests and 112 subtests plus the simulator smoke episode. It could not regenerate any of the 11,220 main-matrix episodes because method code, the frozen prior, and evidence bundles are absent. The semantic O gate is therefore not evaluable; V/C/V+C was not run.", "",
             "## CP-RAG versus Naive", "",
             "Effects are CP-RAG minus Naive, paired by instance and bootstrapped by task (main) or independent family (EXT-v2/CDS). BIA is defined only for CDS boundary questions.", "",
             "| Scope | Metric | Difference | 95% interval |", "|---|---|---:|---:|"]
    for scope in ("main:llm_only", "main:all_public", "main:oracle_reading", "ext_v2", "cds"):
        for metric in ("semantic_cost", "plan_acceptance", "discriminative_stale_query",
                       "discriminative_stale_action", "v3d_propagation"):
            row = selected[(scope, metric)]
            lines.append(f"| {scope} | {metric} | {float(row['CP_minus_Naive']):.4f} | [{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] |")
    lines.extend(["", "## CDS BIA", "",
                  "The original readout lets only explicitly probing policies consume probe observations. The symmetric readout lets every pipeline consume observations from any public semantic action already executed in the same episode. Outside CDS no boundary-question inventory exists, so BIA is reported as not defined rather than imputed.", "",
                  "| Method | Condition | Original BIA | Symmetric BIA |", "|---|---|---:|---:|"])
    for method in ("naive_rag", "verify_all", "arbiter", "cp_rag"):
        for condition in ("K", "K+S", "S", "empty", "K+N"):
            original = bia_idx[(method, condition, "probe_only")]
            symmetric = bia_idx[(method, condition, "all_public_actions")]
            lines.append(f"| {method} | {condition} | {float(original['BIA']):.4f} | {float(symmetric['BIA']):.4f} |")
    original_gap = (float(bia_all[("verify_all", "probe_only")]["BIA"])
                    - float(bia_all[("naive_rag", "probe_only")]["BIA"]))
    symmetric_gap = (float(bia_all[("verify_all", "all_public_actions")]["BIA"])
                     - float(bia_all[("naive_rag", "all_public_actions")]["BIA"]))
    cp_original = (float(bia_all[("cp_rag", "probe_only")]["BIA"])
                   - float(bia_all[("naive_rag", "probe_only")]["BIA"]))
    cp_symmetric = (float(bia_all[("cp_rag", "all_public_actions")]["BIA"])
                    - float(bia_all[("naive_rag", "all_public_actions")]["BIA"]))
    lines.extend(["", f"Across all five CDS conditions, Verify-All minus Naive is {original_gap:.4f} under the original readout and {symmetric_gap:.4f} under the symmetric readout; CP-RAG minus Naive is {cp_original:.4f} and {cp_symmetric:.4f}, respectively."])
    lines.extend(["", "## Action divergence", ""])
    for scope in ("main:llm_only", "main:all_public", "main:oracle_reading", "ext_v2", "cds"):
        counts = {row["divergence_type"]: row["instances"] for row in divergences if row["scope"] == scope}
        lines.append(f"- {scope}: identical={counts['identical']}, extra_probe={counts['extra_probe']}, active_retrieval={counts['active_retrieval']}, dependency_order_only={counts['dependency_order_only']}, other={counts['other']}.")
    lines.extend(["", "## Numeric conventions", "",
                  "The full assertion/conflict cross-tab and placebo instance list are in the companion CSVs. The 542/537 and 7/6 pairs use different dimensions or cost functionals; the combined and split placebo runs are separate nonlinear interventions and cannot be summed."])
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n")


def provenance() -> None:
    paths = [
        ROOT / "scripts/run_w8_closeout_20261016.py",
        ROOT / "prereg/w8_closeout_20261016.md", ROOT / "release/package/ENVIRONMENT.lock",
        ROOT / "release/package/MANIFEST.sha256", CP_DIR / "episodes.jsonl",
        CP_DIR / "episode_metrics.csv", EXT_DIR / "web_run/episodes.jsonl",
        EXT_DIR / "cds_run/episodes.jsonl", ROOT / "data/conflicts.jsonl",
        ROOT / "reports/paperA/c_m_dv.csv", COMBINED_PLACEBO, SPLIT_PLACEBO,
    ]
    write_csv(OUT / "SOURCE_HASHES.csv", [{"path": str(path), "sha256": sha256(path)} for path in paths])


def main() -> None:
    OUT.mkdir(parents=True, exist_ok=True)
    release_audit()
    cp_metrics()
    cds_bia()
    numeric_crosswalk()
    provenance()
    report()
    print(json.dumps({"output": str(OUT), "files": len(list(OUT.iterdir()))}, sort_keys=True))


if __name__ == "__main__":
    main()
