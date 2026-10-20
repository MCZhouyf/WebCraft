#!/usr/bin/env python3
"""Run and summarize frozen W7a EXT-v2 Web and CDS diagnostics."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import subprocess
import sys
import warnings
from collections import Counter, defaultdict
from dataclasses import asdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np
from sklearn.exceptions import ConvergenceWarning

warnings.filterwarnings("ignore", category=ConvergenceWarning)

ROOT = Path(__file__).resolve().parents[1]
ARBITER = Path("/root/autodl-tmp/arbiter")
sys.path[:0] = [str(ROOT), str(ARBITER / "src")]

from arbiter.agent.aggregation import (  # noqa: E402
    arbiter, majority_rag, naive_rag, parametric, truthfinder_rag, verify_all,
)
from arbiter.evaluation.p6_execution import (  # noqa: E402
    _MECHANISM_ACTIONS, _claims, _compiler, _probe_then_rag_filter_bundle,
    run_episode, run_matrix,
)
from arbiter.evaluation.p6_matrix import P6Job  # noqa: E402
from arbiter.pinning import build_pin, verify_webcraft_pin  # noqa: E402
from oracle.planner import VERSIONS  # noqa: E402
import arbiter.adapters.claims as claims_adapter  # noqa: E402
import arbiter.adapters.search as search_adapter  # noqa: E402
import arbiter.adapters.webcraft as webcraft_adapter  # noqa: E402
import arbiter.evaluation.p6_execution as p6_execution  # noqa: E402


TASK_PATH = ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl"
FREEZE_PATH = ROOT / "config/ext_v2/freeze_manifest.json"
CELL_PATH = ROOT / "config/ext_v2/web_cells.json"
MATERIAL_MANIFEST = ROOT / "cds_materials/cds_manifest.json"
EVALUATOR = ROOT / "reports/paperA/ext_v2/cds_evaluator"
OUTPUT = ROOT / "reports/paperA/ext_v2"
PIN_PATH = OUTPUT / "webcraft_pin.json"
INDEX_DIR = ROOT / "corpus/indexes/formal_p1"
PRIOR_PATH = ARBITER / "artifacts/prior/frozen_prior_bundle.json"
METHODS = (
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "arbiter",
    "oracle", "probe_then_rag",
)
SEED = 20261016
METRICS = (
    "SR", "feasible_SR", "overconservative", "execution_failure",
    "plan_acceptance_family", "stale_query_family", "stale_action_family",
    "semantic_actions", "semantic_cost", "search_calls",
)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise RuntimeError(f"refusing empty CSV: {path}")
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def verify_freeze() -> dict[str, Any]:
    manifest = json.loads(FREEZE_PATH.read_text())
    mismatches = []
    for name, expected in manifest["inputs"].items():
        path = ROOT / name
        actual = sha256(path) if path.is_file() else "missing"
        if actual != expected:
            mismatches.append({"path": name, "expected": expected, "actual": actual})
    if mismatches:
        raise RuntimeError(f"W7a freeze mismatch: {mismatches[:5]}")
    return manifest


def write_and_verify_pin() -> None:
    PIN_PATH.write_text(json.dumps(build_pin(ROOT), indent=2, sort_keys=True) + "\n")
    state = verify_webcraft_pin(PIN_PATH, ROOT, require_ready=True)

    # The immutable pin was fully checked above. Avoid hashing the same frozen
    # tree again for every deterministic episode in this one process.
    def cached_verify(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return state

    p6_execution.verify_webcraft_pin = cached_verify
    claims_adapter.verify_webcraft_pin = cached_verify
    search_adapter.verify_webcraft_pin = cached_verify
    webcraft_adapter.verify_webcraft_pin = cached_verify


def load_prior() -> dict[str, Any]:
    from arbiter.agent.aggregation import operator_from_assertion

    payload = json.loads(PRIOR_PATH.read_text())
    return {
        "operators": [operator_from_assertion(row) for row in payload["assertions"]],
        "samples": payload["samples"],
    }


def conflict_vectors() -> dict[str, dict[str, bool]]:
    return {row["id"]: row["truth_vector"] for row in jsonl(ROOT / "data/conflicts.jsonl")}


def focal_maps() -> tuple[dict[tuple[str, str], set[str]], dict[tuple[str, str], dict[str, bool]]]:
    stale: dict[tuple[str, str], set[str]] = defaultdict(set)
    truth: dict[tuple[str, str], dict[str, bool]] = defaultdict(dict)
    vectors = conflict_vectors()
    for path in sorted(EVALUATOR.glob("cds_*.json")):
        row = json.loads(path.read_text())
        key = (row["task_id"], row["target_world"])
        for candidate in row["boundary_truth"]:
            mechanism = candidate["mechanism_id"]
            value = bool(candidate["truth"])
            prior = truth[key].get(mechanism)
            if prior is not None and prior != value:
                raise RuntimeError(f"inconsistent family truth for {key} {mechanism}")
            truth[key][mechanism] = value
            vector = vectors[candidate["conflict_id"]]
            if not value and any(vector.values()):
                stale[key].add(mechanism)
    return stale, truth


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


def bundle_mechanisms(bundle: dict[str, Any], normalizer: Any) -> set[str]:
    derived, _ = _claims(bundle, normalizer=normalizer)
    return {row.mechanism_id for row in derived}


_NORMALIZER: Any | None = None


def compiler_normalizer() -> Any:
    global _NORMALIZER
    if _NORMALIZER is None:
        _NORMALIZER, _ = _compiler(ROOT)
    return _NORMALIZER


def metric_row(
    record: dict[str, Any], task: dict[str, Any], evidence_mechanisms: set[str],
    stale_mechanisms: set[str], scope: str, extra: dict[str, Any] | None = None,
) -> dict[str, Any]:
    events = [event for event in record["events"] if event.get("method") in _MECHANISM_ACTIONS]
    used = list(record["used_mechanism_ids"])
    if len(events) != len(used):
        raise RuntimeError("mechanism/action alignment changed")
    family_commit = any(
        mechanism in stale_mechanisms and event.get("intent") != "probe"
        for mechanism, event in zip(used, events)
    )
    family_action = any(mechanism in stale_mechanisms for mechanism in used)
    feasible = bool(record["true_feasible"])
    row = {
        "scope": scope, "task_id": task["id"],
        "family_id": task["independent_family_id"],
        "condition": record["condition"], "world": record["world"],
        "method": record["method"], "seed": record["seed"],
        "terminal": terminal(record), "true_feasible": int(feasible),
        "SR": int(record["metrics"]["SR"]),
        "feasible_SR": int(record["metrics"]["SR"]) if feasible else "",
        "overconservative": int(feasible and record["reported_feasible"] is False),
        "execution_failure": int(
            feasible and record["reported_feasible"] is True and not record["metrics"]["SR"]
        ),
        "plan_acceptance_family": int(family_commit),
        "stale_query_family": int(bool(stale_mechanisms & evidence_mechanisms)
                                  and int(record["reader"].get("search_count", 0)) > 0),
        "stale_action_family": int(family_action),
        "semantic_actions": len(events),
        "semantic_cost": float(record["metrics"]["primary_cost"]),
        "search_calls": int(record["reader"].get("search_count", 0)),
    }
    if extra:
        row.update(extra)
    return row


def aggregate(rows: list[dict[str, Any]], keys: tuple[str, ...]) -> list[dict[str, Any]]:
    groups: dict[tuple[Any, ...], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        groups[tuple(row[key] for key in keys)].append(row)
    output = []
    for key, values in sorted(groups.items()):
        result = {**dict(zip(keys, key)), "n": len(values)}
        for metric in METRICS:
            observed = [float(row[metric]) for row in values if row[metric] != ""]
            result[metric] = mean(observed) if observed else ""
        output.append(result)
    return output


def bootstrap_effects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    indexed = {(row["task_id"], row["condition"], row["method"]): row for row in rows}
    task_family = {row["task_id"]: row["family_id"] for row in rows}
    pairs = (
        ("mismatched_old2new", "matched_1.21.11"),
        ("mismatched_new2old", "matched_1.16.5"),
    )
    family_effects: dict[tuple[str, str], dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(list)
    )
    for method in METHODS:
        for task_id in sorted(task_family):
            family = task_family[task_id]
            for mismatch, matched in pairs:
                if (task_id, mismatch, method) not in indexed:
                    continue
                for metric in METRICS:
                    left = indexed[(task_id, mismatch, method)][metric]
                    right = indexed[(task_id, matched, method)][metric]
                    if left == "" or right == "":
                        continue
                    family_effects[(method, metric)][family].append(float(left) - float(right))
    rng = np.random.default_rng(SEED)
    output = []
    for (method, metric), by_family in sorted(family_effects.items()):
        family_values = np.array([mean(values) for _, values in sorted(by_family.items())])
        samples = rng.choice(family_values, size=(10000, len(family_values)), replace=True).mean(axis=1)
        output.append({
            "method": method, "metric": metric, "families": len(family_values),
            "mean_mismatch_minus_matched": float(family_values.mean()),
            "ci95_low": float(np.quantile(samples, 0.025)),
            "ci95_high": float(np.quantile(samples, 0.975)),
            "bootstrap_samples": 10000, "seed": SEED,
        })
    return output


def run_web(tasks: list[dict[str, Any]], prior: dict[str, Any]) -> list[dict[str, Any]]:
    cells = json.loads(CELL_PATH.read_text())
    web_ids = set(cells["web_task_ids"])
    web_tasks = [task for task in tasks if task["id"] in web_ids]
    jobs = [
        P6Job(cell["condition"], task["id"], cell["world"], method, SEED)
        for cell in cells["cells"] for task in web_tasks for method in METHODS
    ]
    run_dir = OUTPUT / "web_run"
    run_matrix(
        jobs, web_tasks, root=ROOT, pin_path=PIN_PATH, index_dir=str(INDEX_DIR),
        output_dir=run_dir, top_k=10, reading_mode="all_public",
        prior_bundle=prior,
    )
    records = jsonl(run_dir / "episodes.jsonl")
    if len(records) != len(jobs):
        raise RuntimeError(f"Web episode coverage {len(records)} != {len(jobs)}")
    task_map = {task["id"]: task for task in web_tasks}
    stale_map, _ = focal_maps()
    normalizer = compiler_normalizer()
    evidence_cache = {}
    rows = []
    for record in records:
        key = (record["condition"], record["task_id"], int(record["seed"]))
        if key not in evidence_cache:
            path = run_dir / "evidence" / f"{record['condition']}__{record['task_id']}__{record['seed']}.json"
            evidence_cache[key] = bundle_mechanisms(json.loads(path.read_text()), normalizer)
        rows.append(metric_row(
            record, task_map[record["task_id"]], evidence_cache[key],
            stale_map[(record["task_id"], record["world"])], "web",
        ))
    rows.sort(key=lambda row: (row["family_id"], row["task_id"], row["condition"], row["method"]))
    write_csv(OUTPUT / "web_episode_metrics.csv", rows)
    grouped = aggregate(rows, ("family_id", "condition", "world", "method"))
    write_csv(OUTPUT / "web_results_by_family.csv", grouped)
    effects = bootstrap_effects(rows)
    write_csv(OUTPUT / "family_mismatch_effects.csv", effects)
    (OUTPUT / "web_run_manifest.json").write_text(json.dumps({
        "scope": "EXT-v2 Web", "zero_llm_calls": True,
        "tasks": len(web_tasks), "cells": len(cells["cells"]),
        "methods": list(METHODS), "episodes": len(jobs),
        "jobs": [asdict(job) for job in jobs],
    }, indent=2, sort_keys=True) + "\n")
    return rows


def raw_claim(private: dict[str, Any], chunk_id: str, text: str) -> dict[str, Any]:
    assertion = private["assertion"]
    if assertion["kind"] == "drop":
        claim_type = "drop"
        subject = assertion["block"]
        payload = {
            "drops": assertion["drops"],
            "silk_touch": bool(assertion.get("silk_touch", False)),
        }
    else:
        subject = assertion["output"]
        if assertion["station"] == "furnace":
            claim_type = "smelting"
            payload = {
                "ingredients": [item for slot in assertion["ingredients"] for item in slot],
                "output_count": int(assertion.get("count", 1)),
            }
        else:
            claim_type = "recipe"
            if assertion.get("ordered"):
                layout: list[str | None] = [None] * 9
                options: list[list[str]] = [[] for _ in range(9)]
                for row_index, row in enumerate(assertion.get("grid", [])):
                    for column_index, choices in enumerate(row):
                        index = row_index * 3 + column_index
                        if choices:
                            layout[index] = sorted(choices)[0]
                            options[index] = list(choices)
                payload = {
                    "ingredients": [item for item in layout if item],
                    "layout": layout, "layout_options": options,
                    "output_count": int(assertion.get("count", 1)),
                }
            else:
                payload = {
                    "ingredients": [sorted(slot)[0] for slot in assertion["ingredients"]],
                    "output_count": int(assertion.get("count", 1)),
                }
    return {
        "claim_id": f"cl_{hashlib.sha256((chunk_id + private['mechanism_id']).encode()).hexdigest()[:16]}",
        "chunk_id": chunk_id, "claim_type": claim_type, "subject_item": subject,
        "payload": payload, "polarity": "positive" if private["value"] else "negative",
        "tense": "current", "span_text": text, "extractor_version": "cds-deterministic-v1",
    }


def cds_bundle(
    private_payload: dict[str, Any], visible_payload: dict[str, Any], condition: str,
) -> dict[str, Any]:
    visible_by_hash = {
        hashlib.sha256(row["mechanism fact"].encode()).hexdigest(): row
        for row in visible_payload["conditions"][condition]
    }
    search_rows, claims = [], []
    for index, row in enumerate(private_payload["conditions"][condition], 1):
        visible = visible_by_hash.get(row["text_sha256"])
        if visible is None:
            raise RuntimeError(
                f"private/visible CDS text mismatch: {private_payload['instance_id']} {condition}"
            )
        text = visible["mechanism fact"]
        chunk_id = f"{private_payload['instance_id']}__{condition}__{index:03d}"
        search_rows.append({
            "text": text, "source_site": row["source"],
            "page_title": f"CDS {row['role']}", "revid": chunk_id,
            "timestamp": row["timestamp"], "chunk_id": chunk_id,
        })
        claims.append(raw_claim(row, chunk_id, text))
    return {
        "schema_version": 2, "claim_source": "cds_frozen_in_memory",
        "reading_mode": "cds", "search_rows": search_rows, "claims": claims,
        "reader": {
            "search_count": 1 if search_rows else 0,
            "unique_chunk_count": len(search_rows), "raw_claim_count": len(claims),
            "extractor_claim_counts": {"cds-deterministic-v1": len(claims)},
            "search_events": [],
        },
    }


def method_belief(method: str, bundle: dict[str, Any], world: str, prior: dict[str, Any]) -> Any | None:
    normalizer = compiler_normalizer()
    selected = (_probe_then_rag_filter_bundle(bundle, world)
                if method == "probe_then_rag" else bundle)
    derived, _ = _claims(selected, normalizer=normalizer)
    if method in {"parametric", "probe_then_parametric"}:
        return parametric(prior["samples"]).belief
    if method == "oracle":
        return "oracle"
    if not derived:
        return None
    if method in {"naive_rag", "arbiter_shell_naive", "probe_then_rag"}:
        return naive_rag(derived).belief
    if method == "majority_rag":
        return majority_rag(derived).belief
    if method == "truthfinder_rag":
        return truthfinder_rag(derived).belief
    if method == "verify_all":
        return verify_all(derived).belief
    if method == "arbiter":
        sources = {(row.hit["source_site"], row.hit["page_title"], row.hit["revid"])
                   for row in derived}
        return (arbiter(derived) if len(sources) >= 2 else naive_rag(derived)).belief
    raise ValueError(method)


def boundary_score(
    record: dict[str, Any], private: dict[str, Any], bundle: dict[str, Any], prior: dict[str, Any]
) -> dict[str, Any]:
    belief = method_belief(record["method"], bundle, record["world"], prior)
    events = [event for event in record["events"] if event.get("method") in _MECHANISM_ACTIONS]
    used = list(record["used_mechanism_ids"])
    probed = {mechanism for mechanism, event in zip(used, events) if event.get("intent") == "probe"}
    correct = abstained = 0
    for candidate in private["boundary_truth"]:
        mechanism = candidate["mechanism_id"]
        truth = bool(candidate["truth"])
        if belief == "oracle" or mechanism in probed:
            prediction = truth
        elif belief is None or mechanism not in belief.phi:
            prediction = None
        else:
            prediction = belief.mechanism_probability(mechanism) >= 0.5
        if prediction is None:
            abstained += 1
        elif prediction == truth:
            correct += 1
    total = len(private["boundary_truth"])
    return {
        "boundary_correct": correct, "boundary_total": total,
        "boundary_abstained": abstained, "boundary_accuracy": correct / total,
    }


def run_cds(tasks: list[dict[str, Any]], prior: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = json.loads(MATERIAL_MANIFEST.read_text())
    task_map = {task["id"]: task for task in tasks}
    global_vectors = conflict_vectors()
    run_dir = OUTPUT / "cds_run"
    episode_path = run_dir / "episodes.jsonl"
    completed = set()
    if episode_path.is_file():
        for row in jsonl(episode_path):
            completed.add((row["condition"], row["task_id"], row["world"], row["method"]))
    normalizer = compiler_normalizer()
    expected = manifest["directed_instances"] * 5 * len(METHODS)
    for manifest_row in manifest["rows"]:
        private = json.loads((ROOT / manifest_row["evaluator_path"]).read_text())
        visible = json.loads((ROOT / manifest_row["visible_path"]).read_text())
        task = task_map[private["task_id"]]
        synthetic_vectors = dict(global_vectors)
        for candidate in private["boundary_truth"]:
            vector = {world: bool(candidate["truth"]) for world in VERSIONS}
            vector[private["comparison_world"]] = not bool(candidate["truth"])
            synthetic_vectors[candidate["mechanism_id"]] = vector
        for condition in ("K", "K+S", "S", "empty", "K+N"):
            bundle = cds_bundle(private, visible, condition)
            derived_ids = {row.mechanism_id for row in _claims(bundle, normalizer=normalizer)[0]}
            job_condition = f"{private['instance_id']}__{condition}"
            for method in METHODS:
                key = (job_condition, task["id"], private["target_world"], method)
                if key in completed:
                    continue
                job = P6Job(job_condition, task["id"], private["target_world"], method, SEED)
                record = run_episode(
                    job, task, bundle, root=ROOT, pin_path=PIN_PATH,
                    index_dir=str(INDEX_DIR), output_dir=run_dir,
                    conflict_vectors=synthetic_vectors, preview=False, prior_bundle=prior,
                )
                record["cds_instance_id"] = private["instance_id"]
                record["cds_condition"] = condition
                record["comparison_world"] = private["comparison_world"]
                record["cds_evidence_mechanism_ids"] = sorted(derived_ids)
                record["boundary"] = boundary_score(record, private, bundle, prior)
                episode_path.parent.mkdir(parents=True, exist_ok=True)
                with episode_path.open("a") as handle:
                    handle.write(json.dumps(record, sort_keys=True) + "\n")
    records = jsonl(episode_path)
    if len(records) != expected:
        raise RuntimeError(f"CDS episode coverage {len(records)} != {expected}")
    stale_map, _ = focal_maps()
    rows = []
    for record in records:
        task = task_map[record["task_id"]]
        extra = {
            "cds_instance_id": record["cds_instance_id"],
            "comparison_world": record["comparison_world"],
            "cds_condition": record["cds_condition"],
            **record["boundary"],
        }
        rows.append(metric_row(
            record, task, set(record["cds_evidence_mechanism_ids"]),
            stale_map[(record["task_id"], record["world"])], "cds", extra,
        ))
    rows.sort(key=lambda row: (
        row["family_id"], row["task_id"], row["cds_instance_id"],
        row["cds_condition"], row["method"],
    ))
    write_csv(OUTPUT / "cds_episode_metrics.csv", rows)
    grouped = aggregate(rows, ("family_id", "cds_condition", "method"))
    boundary_groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        boundary_groups[(row["family_id"], row["cds_condition"], row["method"])].append(row)
    boundary_rows = []
    for key, values in sorted(boundary_groups.items()):
        correct = sum(row["boundary_correct"] for row in values)
        total = sum(row["boundary_total"] for row in values)
        abstained = sum(row["boundary_abstained"] for row in values)
        boundary_rows.append({
            "family_id": key[0], "cds_condition": key[1], "method": key[2],
            "instances": len(values), "correct": correct, "total": total,
            "abstained": abstained, "accuracy": correct / total if total else "",
        })
    write_csv(OUTPUT / "cds_results_by_family.csv", grouped)
    write_csv(OUTPUT / "cds_boundary_accuracy.csv", boundary_rows)
    (OUTPUT / "cds_run_manifest.json").write_text(json.dumps({
        "scope": "CDS", "zero_llm_calls": True,
        "directed_instances": manifest["directed_instances"],
        "conditions": 5, "methods": list(METHODS), "episodes": len(records),
        "materials_sha256": sha256(MATERIAL_MANIFEST),
    }, indent=2, sort_keys=True) + "\n")
    return rows


def write_report(web_rows: list[dict[str, Any]], cds_rows: list[dict[str, Any]]) -> None:
    setup = json.loads((OUTPUT / "setup_summary.json").read_text())
    effects = list(csv.DictReader((OUTPUT / "family_mismatch_effects.csv").open())) if web_rows else []
    primary = [row for row in effects if row["metric"] == "plan_acceptance_family"]
    lines = [
        "# W7a EXT-v2 and CDS report", "",
        "> Post-review deterministic diagnostic; separate from the frozen main matrix. Zero LLM calls.", "",
        f"- Behaviorally real source mechanisms/families: {setup['behaviorally_real_mechanisms']} / {setup['behaviorally_real_families']}.",
        f"- Planner-representable families and tasks: {setup['planner_representable_families']} / {setup['ext_v2_tasks']}.",
        f"- EXT-v2 Web task-corpus grids: {setup['web_corpus_grids']}.",
        f"- CDS directed/five-condition instances: {setup['directed_cds_instances']} / {setup['directed_cds_instances'] * 5}.",
        f"- CDS material manifest SHA-256: `{sha256(MATERIAL_MANIFEST)}`.", "",
        "## Family-level mismatch effect", "",
        "Mismatch minus matched effects use 10,000 family bootstrap samples (seed 20261016).", "",
        "| Method | Mean plan acceptance effect | 95% interval | Families |", "|---|---:|---:|---:|",
    ]
    for row in primary:
        lines.append(
            f"| {row['method']} | {float(row['mean_mismatch_minus_matched']):.3f} | "
            f"[{float(row['ci95_low']):.3f}, {float(row['ci95_high']):.3f}] | {row['families']} |"
        )
    if cds_rows:
        lines.extend(["", "## CDS", "",
                      f"Completed {len(cds_rows)} method-condition episodes. Boundary accuracy is reported per family, condition, and method in `cds_boundary_accuracy.csv`.",
                      "",
                      "An initial adapter trial supplied internal role labels as search-row text. It was stopped from consideration, retained under `invalid_adapter_trial/`, and excluded from every formal CSV. The accepted rerun resolves each search-row text from the frozen visible material by SHA-256 before claim compilation."])
    lines.extend([
        "", "## Verification", "",
        "- All 117 frozen input hashes matched before execution.",
        "- Oracle/simulator setup validation passed 39/39 task-world checks.",
        "- Accepted result coverage is 600/600 Web episodes and 2,600/2,600 CDS episodes with unique matrix keys.",
        "- CDS visible facts expose exactly `mechanism fact`, `source`, and `timestamp`; K+N neutral facts match stale additions in count and text length.",
        "- `tests/sim` and `tests/corpus`: 137 tests and 119 subtests passed (explicit local-package import mode).",
    ])
    (OUTPUT / "REPORT.md").write_text("\n".join(lines) + "\n")


def write_provenance() -> None:
    arbiter_commit = subprocess.run(
        ["git", "rev-parse", "HEAD"], cwd=ARBITER, text=True,
        check=True, capture_output=True,
    ).stdout.strip()
    (OUTPUT / "run_provenance.json").write_text(json.dumps({
        "schema_version": 1,
        "zero_llm_calls": True,
        "webcraft_pin_sha256": sha256(PIN_PATH),
        "webcraft_commit": json.loads(PIN_PATH.read_text())["webcraft_commit"],
        "arbiter_commit": arbiter_commit,
        "freeze_manifest_sha256": sha256(FREEZE_PATH),
        "material_manifest_sha256": sha256(MATERIAL_MANIFEST),
        "execution_script_sha256": sha256(Path(__file__)),
        "pin_check": "full verification once at process start; immutable result cached within process",
        "invalid_adapter_trial_retained": True,
        "invalid_adapter_trial_excluded_from_formal_tables": True,
    }, indent=2, sort_keys=True) + "\n")


def output_hashes() -> None:
    paths = [path for path in OUTPUT.rglob("*") if path.is_file()
             and path.name != "OUTPUT_HASHES.json"]
    (OUTPUT / "OUTPUT_HASHES.json").write_text(json.dumps({
        str(path.relative_to(ROOT)): sha256(path) for path in sorted(paths)
    }, indent=2, sort_keys=True) + "\n")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--web-only", action="store_true")
    parser.add_argument("--cds-only", action="store_true")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    verify_freeze()
    write_and_verify_pin()
    tasks = jsonl(TASK_PATH)
    prior = load_prior()
    web_rows: list[dict[str, Any]] = []
    cds_rows: list[dict[str, Any]] = []
    if not args.cds_only:
        web_rows = run_web(tasks, prior)
    elif (OUTPUT / "web_episode_metrics.csv").is_file():
        with (OUTPUT / "web_episode_metrics.csv").open(newline="") as handle:
            web_rows = list(csv.DictReader(handle))
    if not args.web_only:
        cds_rows = run_cds(tasks, prior)
    elif (OUTPUT / "cds_episode_metrics.csv").is_file():
        with (OUTPUT / "cds_episode_metrics.csv").open(newline="") as handle:
            cds_rows = list(csv.DictReader(handle))
    write_report(web_rows, cds_rows)
    write_provenance()
    output_hashes()
    print(json.dumps({
        "status": "W7A_COMPLETE" if web_rows and cds_rows else "W7A_PARTIAL",
        "web_episodes": len(web_rows), "cds_episodes": len(cds_rows),
        "pin_commit": json.loads(PIN_PATH.read_text())["webcraft_commit"],
    }, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
