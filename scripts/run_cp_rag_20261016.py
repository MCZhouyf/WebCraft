#!/usr/bin/env python3
"""Develop, freeze, and evaluate the preregistered version-blind CP-RAG row."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import sys
import tempfile
from collections import Counter, defaultdict
from dataclasses import replace
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARB = Path("/root/autodl-tmp/arbiter")
sys.path[:0] = [str(ROOT), str(ROOT / "scripts"), str(ARB / "src"), str(ARB / "scripts")]

from arbiter.agent.core import ArbiterAgent, ExecutionPolicy  # noqa: E402
from arbiter.agent.planning import BeliefPlanner, action_signature  # noqa: E402
from arbiter.agent.aggregation import naive_rag  # noqa: E402
from arbiter.evaluation.main_matrix import MAIN_CELLS  # noqa: E402
from arbiter.evaluation.p6_execution import (  # noqa: E402
    _claims, _compiler, build_evidence, load_conflict_vectors, run_episode,
)
from arbiter.evaluation.p6_matrix import P6Job  # noqa: E402
from arbiter.llm import CachedClaimExtractor  # noqa: E402
from arbiter.pinning import build_pin, verify_webcraft_pin  # noqa: E402
import arbiter.adapters.claims as claims_adapter  # noqa: E402
import arbiter.adapters.search as search_adapter  # noqa: E402
import arbiter.adapters.webcraft as webcraft_adapter  # noqa: E402
import arbiter.evaluation.p6_execution as p6  # noqa: E402
import run_main  # type: ignore  # noqa: E402
import run_ext_v2_cds_20261016 as w7a  # type: ignore  # noqa: E402


OUT = ROOT / "reports/paperA/review_v14_zero_call/cp_rag"
INDEX = ROOT / "corpus/indexes/formal_p1"
SEED = 20261016
SOURCE_SEED = 20261006


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"empty CSV: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader(); writer.writerows(rows)


def relation_key(operator: Any) -> tuple[str, str]:
    if operator.method == "mine":
        return ("drop", str(operator.params["node_type"]))
    if operator.method == "check_name":
        return ("name", str(operator.params["identifier"]))
    return (operator.method, str(operator.output))


def cp_solve(self: ArbiterAgent, *, policy: ExecutionPolicy | None = None,
             strategy: Any = None) -> dict[str, Any]:
    """Frozen conflict-only verification policy; no version-side information."""
    if self._belief is None:
        raise RuntimeError("agent is not configured")
    policy = policy or ExecutionPolicy()
    self._used_mechanisms = []
    self._explicit_probes = []
    blocked: set[str] = set()
    traces: list[dict[str, Any]] = []
    spent = 0
    last_failed_key: tuple[str, str] | None = None
    researched: set[tuple[str, str]] = set()

    groups: dict[tuple[str, str], set[str]] = defaultdict(set)
    signatures: dict[tuple[str, str], set[str]] = defaultdict(set)
    for operator in self._operators:
        key = relation_key(operator)
        groups[key].add(operator.mechanism_id)
        signatures[key].add(json.dumps({
            "method": operator.method, "params": operator.params,
            "inputs": operator.input_options, "output": operator.output,
            "count": operator.output_count,
        }, sort_keys=True))
    conflicting = {key for key, values in signatures.items() if len(values) >= 2}

    def finish(success: bool, feasible: bool | None) -> dict[str, Any]:
        self.api.submit({"feasible": feasible})
        return {
            "success": success, "feasible": feasible,
            "used_mechanism_ids": list(self._used_mechanisms),
            "probe_mechanism_ids": list(self._explicit_probes),
            "policy_trace": traces,
        }

    while len(self._used_mechanisms) < policy.max_actions and spent < policy.max_actions:
        self.kit = Counter({item["name"]: item["count"] for item in self.api.inventory()})
        if self.kit[self.goal.item] >= self.goal.count:
            return finish(True, True)
        operators = [replace(op, probability=self._belief.mechanism_probability(op.mechanism_id))
                     for op in self._operators]
        planner = BeliefPlanner(
            operators, self.kit, uncertainty_weight=policy.uncertainty_weight,
            action_limit=policy.max_actions - spent,
            inventory_aware=policy.inventory_aware,
            blocked_actions=frozenset(blocked),
        )
        plans = planner.candidates(self.goal.item, self.goal.count, policy.n_plans)
        if not plans:
            callback = getattr(self, "_cp_research", None)
            if last_failed_key is not None and last_failed_key not in researched and callback is not None:
                researched.add(last_failed_key)
                additions = callback(last_failed_key[1], self.goal.item)
                existing = {op.mechanism_id for op in self._operators}
                new_ops = [op for op, _ in additions if op.mechanism_id not in existing]
                for operator, probability in additions:
                    if operator.mechanism_id in existing:
                        continue
                    self._belief.phi[operator.mechanism_id] = np.full(
                        len(self._belief.q), float(probability)
                    )
                    key = relation_key(operator)
                    groups[key].add(operator.mechanism_id)
                    signatures[key].add(json.dumps({
                        "method": operator.method, "params": operator.params,
                        "inputs": operator.input_options, "output": operator.output,
                        "count": operator.output_count,
                    }, sort_keys=True))
                    if len(signatures[key]) >= 2:
                        conflicting.add(key)
                self._operators = tuple(self._operators) + tuple(new_ops)
                traces.append({
                    "decision": "research", "research_triggered": True,
                    "denied_subject": last_failed_key[1], "new_candidates": len(new_ops),
                })
                if new_ops:
                    continue
            traces.append({"decision": "infeasible", "research_triggered": False})
            return finish(False, False)
        step = plans[0].steps[0]
        operator = next(op for op in self._operators if op.mechanism_id == step.mechanism_id)
        key = relation_key(operator)
        observed_before = step.mechanism_id in (self._belief.observations or {})
        probe = key in conflicting and not observed_before and len(self._explicit_probes) < policy.max_probes
        signature = action_signature(step.method, step.params, step.expected_output)
        if signature in blocked or spent + step.cost > policy.max_actions:
            return finish(False, None)
        intent = "probe" if probe else "commit"
        result = self._call(step.method, step.params, intent=intent)
        self._used_mechanisms.append(step.mechanism_id)
        if probe:
            self._explicit_probes.append(step.mechanism_id)
        spent += int(result.get("cost", {}).get("primary", step.cost))
        observed = result.get("success") is not False and step.expected_output in self._produced_names(result)
        resource_failure = result.get("error") in {"missing ingredient", "insufficient fuel"}
        if not resource_failure and (probe or not observed):
            self._belief.update(step.mechanism_id, observed)
        if not observed:
            blocked.add(signature)
            last_failed_key = key
        traces.append({
            "decision": "probe_conflict" if probe else "execute_naive",
            "intent": intent, "mechanism_id": step.mechanism_id,
            "relation_key": list(key), "candidate_count": len(groups[key]),
            "payload_count": len(signatures[key]), "observed": observed,
            "resource_failure": resource_failure, "research_triggered": False,
        })
    return finish(False, None)


def setup() -> tuple[Path, dict[str, Any], dict[str, dict[str, bool]]]:
    OUT.mkdir(parents=True, exist_ok=True)
    pin = OUT / "webcraft_pin.json"
    pin.write_text(json.dumps(build_pin(ROOT), indent=2, sort_keys=True) + "\n")
    state = verify_webcraft_pin(pin, ROOT, require_ready=True)

    def cached(*args: Any, **kwargs: Any) -> dict[str, Any]: return state
    p6.verify_webcraft_pin = cached
    claims_adapter.verify_webcraft_pin = cached
    search_adapter.verify_webcraft_pin = cached
    webcraft_adapter.verify_webcraft_pin = cached

    class ForbiddenGateway:
        def __init__(self, *args: Any, **kwargs: Any):
            raise RuntimeError("CP-RAG is zero-call")
    p6.GatewayClient = ForbiddenGateway
    prior, _ = run_main.load_prior_bundle(ARB / "artifacts/prior/frozen_prior_bundle.json")
    return pin, prior, load_conflict_vectors(ROOT / "data/conflicts.jsonl")


def public_tasks(path: Path, split: str | None = None) -> list[dict[str, Any]]:
    standard = json.loads((ROOT / "config/resource_model.json").read_text())["standard_inventory"]
    rows = jsonl(path)
    if split is not None:
        rows = [row for row in rows if row.get("split") == split]
    return [{
        "id": row["id"], "target": row["target"], "feasible": row["feasible"],
        "conflict_ids": row["conflict_ids"], "standard_items": standard,
        "task_supplements": row.get("task_supplements", []),
    } for row in sorted(rows, key=lambda row: row["id"])]


def run_cp(job: P6Job, task: dict[str, Any], bundle: dict[str, Any], *, pin: Path,
           prior: dict[str, Any], vectors: dict[str, dict[str, bool]], output: Path) -> dict[str, Any]:
    surrogate = P6Job(job.condition, job.task_id, job.world, "naive_rag", job.seed)
    normalizer, _ = _compiler(ROOT)
    research_cache: dict[tuple[str, str], list[tuple[Any, float]]] = {}

    def research(subject: str, goal: str) -> list[tuple[Any, float]]:
        cache_key = (subject, goal)
        if cache_key in research_cache:
            return research_cache[cache_key]
        # CDS and evaluator-projected reading have no public index corresponding
        # to their injected condition. Their fixed bundle is the complete search space.
        if (bundle.get("reading_mode") not in {"all_public", "llm_only"}
                or job.condition not in {path.name for path in INDEX.iterdir() if path.is_dir()}):
            research_cache[cache_key] = []
            return []
        backend = p6.load_search_backend(
            job.condition, str(INDEX / job.condition), root=ROOT, pin_path=pin,
        )
        cache = p6.load_claims_cache(
            str(INDEX / job.condition), root=ROOT, pin_path=pin,
            reading_mode=str(bundle.get("reading_mode", "all_public")),
            llm_extractor_version=str(p6._reader_config()["llm_extractor_version"]),
        )
        hits = backend.search(f"{subject} {goal}", 10)
        claims = [claim for hit in hits for claim in (cache.get(str(hit["chunk_id"])) if cache else [])]
        mini = {**bundle, "search_rows": hits, "claims": claims}
        derived, operators = _claims(mini, normalizer=normalizer)
        probability = {claim.mechanism_id: (0.99 if claim.value else 0.01) for claim in derived}
        research_cache[cache_key] = [(op, probability.get(op.mechanism_id, .5)) for op in operators]
        return research_cache[cache_key]

    original_solve = ArbiterAgent.solve
    original_init = ArbiterAgent.__init__

    def cp_init(self: ArbiterAgent, *args: Any, **kwargs: Any) -> None:
        original_init(self, *args, **kwargs)
        self._cp_research = research

    ArbiterAgent.solve = cp_solve
    ArbiterAgent.__init__ = cp_init
    try:
        record = run_episode(
            surrogate, task, bundle, root=ROOT, pin_path=pin, index_dir=str(INDEX),
            output_dir=output, conflict_vectors=vectors, preview=False, prior_bundle=prior,
        )
    finally:
        ArbiterAgent.solve = original_solve
        ArbiterAgent.__init__ = original_init
    record["method"] = "cp_rag"
    record["method_version"] = "cp-rag-w7b-frozen-v1"
    if any(event["cost"].get("model_calls", 0) for event in record["events"]):
        raise RuntimeError("CP-RAG made a model call")
    return record


def normalized_actions(record: dict[str, Any]) -> list[str]:
    return [json.dumps({
        "method": event.get("method"), "params": event.get("params", {}),
        "intent": event.get("intent"),
    }, sort_keys=True) for event in record["events"]]


def develop() -> None:
    pin, prior, vectors = setup()
    all_tasks = jsonl(ROOT / "data/tasks.jsonl")
    raw = [row for row in all_tasks if row["split"] == "dev"]
    tasks = public_tasks(ROOT / "data/tasks.jsonl", "dev")
    task_map = {row["id"]: row for row in tasks}
    if len(task_map) != 8:
        raise RuntimeError("CP development is restricted to frozen dev8")
    normalizer, _ = _compiler(ROOT)
    caches = {condition: p6.load_claims_cache(str(INDEX / condition), root=ROOT,
                                               pin_path=pin, reading_mode="all_public")
              for condition, _ in MAIN_CELLS[:5]}
    bundles = {}
    for condition, _ in MAIN_CELLS[:5]:
        for task in raw:
            key = (condition, task["id"])
            bundles[key] = build_evidence(
                task, condition, index_dir=str(INDEX), root=ROOT, pin_path=pin,
                claims_cache=caches[condition], fallback=None,
                fallback_dir=OUT / "forbidden_fallback", top_k=10,
            )
            bundles[key]["reading_mode"] = "all_public"
    signatures = []
    with tempfile.TemporaryDirectory(prefix="cp_dev_", dir="/root/autodl-tmp") as directory:
        base = Path(directory)
        for repeat in (1, 2):
            for condition, world in MAIN_CELLS[:5]:
                for task_id in sorted(task_map):
                    job = P6Job(condition, task_id, world, "cp_rag", SEED)
                    record = run_cp(job, task_map[task_id], bundles[(condition, task_id)],
                                    pin=pin, prior=prior, vectors=vectors,
                                    output=base / f"repeat_{repeat}")
                    signatures.append({
                        "repeat": repeat, "condition": condition, "world": world,
                        "task_id": task_id, "terminal": terminal(record),
                        "actions_sha256": hashlib.sha256(
                            "\n".join(normalized_actions(record)).encode()).hexdigest(),
                        "research_triggers": sum(
                            int(trace.get("research_triggered", False))
                            for trace in record.get("policy_trace", [])
                        ),
                    })
    first = {(r["condition"], r["world"], r["task_id"]): r for r in signatures if r["repeat"] == 1}
    second = {(r["condition"], r["world"], r["task_id"]): r for r in signatures if r["repeat"] == 2}
    if {(k, v["terminal"], v["actions_sha256"]) for k, v in first.items()} != {
        (k, v["terminal"], v["actions_sha256"]) for k, v in second.items()
    }:
        raise RuntimeError("CP dev repeats were not deterministic")
    write_csv(OUT / "dev8_determinism.csv", signatures)
    (OUT / "dev_freeze.json").write_text(json.dumps({
        "dev_tasks": 8, "cells": 5, "episodes_per_repeat": 40,
        "byte_action_deterministic": True,
        "implementation_sha256": sha256(Path(__file__)),
        "research_triggers": sum(r["research_triggers"] for r in signatures),
    }, indent=2, sort_keys=True) + "\n")


def terminal(record: dict[str, Any]) -> str:
    if record["metrics"]["SR"]: return "success"
    if record["true_feasible"] and record["reported_feasible"] is False: return "overconservative"
    if record["true_feasible"] and record["reported_feasible"] is True: return "execution_failure"
    if not record["true_feasible"] and record["reported_feasible"] is False: return "correct_infeasible"
    if not record["true_feasible"] and record["reported_feasible"] is True: return "false_feasible"
    return "undetermined"


def metric(record: dict[str, Any], scope: str, family: str = "") -> dict[str, Any]:
    feasible = bool(record["true_feasible"])
    return {
        "scope": scope, "family_id": family, "condition": record["condition"],
        "world": record["world"], "task_id": record["task_id"], "method": "cp_rag",
        "terminal": terminal(record), "true_feasible": int(feasible),
        "SR": int(record["metrics"]["SR"]),
        "feasible_SR": int(record["metrics"]["SR"]) if feasible else "",
        "overconservative": int(feasible and record["reported_feasible"] is False),
        "execution_failure": int(feasible and record["reported_feasible"] is True and not record["metrics"]["SR"]),
        "plan_acceptance": int(record["metrics"]["HTR_commit"]),
        "stale_action": int(record["metrics"]["HTR_all"]),
        "stale_query": int(record["metrics"]["HTR_all"] and record["reader"].get("search_count", 0) > 0),
        "semantic_actions": sum(event.get("method") in p6._MECHANISM_ACTIONS for event in record["events"]),
        "semantic_cost": float(record["metrics"]["primary_cost"]),
        "search_calls": int(record["reader"].get("search_count", 0)),
    }


def baselines(scope: str, mode: str = "") -> dict[tuple[str, str, str, str], dict[str, Any]]:
    if scope == "main":
        if mode == "oracle_reading":
            rows = jsonl(ARB / "artifacts/post_review/oracle_reading/episodes.jsonl")
        else:
            rows = jsonl(ARB / f"artifacts/main/{mode}/episodes.jsonl")
            rows += jsonl(ARB / f"artifacts/post_run_probe_then_rag/{mode}/episodes.jsonl")
        return {(r["condition"], r["world"], r["task_id"], r["method"]): r
                for r in rows if int(r["seed"]) == SOURCE_SEED}
    path = ROOT / f"reports/paperA/ext_v2/{scope}_run/episodes.jsonl"
    rows = jsonl(path)
    return {(r["condition"], r["world"], r["task_id"], r["method"]): r for r in rows}


def evaluate_main(pin: Path, prior: dict[str, Any], vectors: dict[str, dict[str, bool]],
                  output: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tasks = run_main.load_evaluation_tasks(ROOT / "data/tasks.jsonl", ROOT / "config/resource_model.json")
    task_map = {row["id"]: row for row in tasks}
    records, rows = [], []
    for mode in ("llm_only", "all_public", "oracle_reading"):
        base = baselines("main", mode)
        for condition, world in MAIN_CELLS:
            for task_id in sorted(task_map):
                if mode == "oracle_reading":
                    path = ARB / f"artifacts/post_review/oracle_reading/evidence/{condition}__{task_id}.json"
                else:
                    path = ARB / f"artifacts/main/{mode}/evidence/{condition}__{task_id}__{SOURCE_SEED}.json"
                bundle = json.loads(path.read_text())
                job = P6Job(condition, task_id, world, "cp_rag", SEED)
                record = run_cp(job, task_map[task_id], bundle, pin=pin, prior=prior,
                                vectors=vectors, output=output / mode)
                row = metric(record, f"main:{mode}")
                for method in ("naive_rag", "verify_all"):
                    peer = base[(condition, world, task_id, method)]
                    row[f"action_equal_{method}"] = int(normalized_actions(record) == normalized_actions(peer))
                    row[f"SR_delta_vs_{method}"] = int(record["metrics"]["SR"]) - int(peer["metrics"]["SR"])
                rows.append(row); records.append(record)
    return rows, records


def evaluate_ext(pin: Path, prior: dict[str, Any], vectors: dict[str, dict[str, bool]],
                 output: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tasks = jsonl(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
    task_map = {row["id"]: row for row in tasks}
    public = public_tasks(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
    public_map = {row["id"]: row for row in public}
    cells = json.loads((ROOT / "config/ext_v2/web_cells.json").read_text())["cells"]
    base = baselines("web")
    rows, records = [], []
    for cell in cells:
        for task_id in sorted(public_map):
            path = ROOT / f"reports/paperA/ext_v2/web_run/evidence/{cell['condition']}__{task_id}__{SEED}.json"
            if not path.is_file(): continue
            bundle = json.loads(path.read_text())
            record = run_cp(P6Job(cell["condition"], task_id, cell["world"], "cp_rag", SEED),
                            public_map[task_id], bundle, pin=pin, prior=prior, vectors=vectors,
                            output=output / "ext_v2")
            row = metric(record, "ext_v2", task_map[task_id]["independent_family_id"])
            for method in ("naive_rag", "verify_all"):
                peer = base[(cell["condition"], cell["world"], task_id, method)]
                row[f"action_equal_{method}"] = int(normalized_actions(record) == normalized_actions(peer))
                row[f"SR_delta_vs_{method}"] = int(record["metrics"]["SR"]) - int(peer["metrics"]["SR"])
            rows.append(row); records.append(record)
    return rows, records


def evaluate_cds(pin: Path, prior: dict[str, Any], vectors: dict[str, dict[str, bool]],
                 output: Path) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    tasks = jsonl(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
    public = public_tasks(ROOT / "data/ext_v2/ext_v2_tasks_20261016.jsonl")
    task_map = {row["id"]: row for row in public}
    raw_map = {row["id"]: row for row in tasks}
    manifest = json.loads((ROOT / "cds_materials/cds_manifest.json").read_text())
    base = baselines("cds")
    rows, records = [], []
    for item in manifest["rows"]:
        private = json.loads((ROOT / item["evaluator_path"]).read_text())
        visible = json.loads((ROOT / item["visible_path"]).read_text())
        for condition in ("K", "K+S", "S", "empty", "K+N"):
            bundle = w7a.cds_bundle(private, visible, condition)
            synthetic = dict(vectors)
            for candidate in private["boundary_truth"]:
                synthetic[candidate["mechanism_id"]] = {
                    world: bool(candidate["truth"]) for world in ("1.16.5", "1.19", "1.21.11")
                }
            record = run_cp(P6Job(condition, private["task_id"], private["target_world"],
                                  "cp_rag", SEED), task_map[private["task_id"]], bundle,
                            pin=pin, prior=prior, vectors=synthetic, output=output / "cds")
            row = metric(record, "cds", raw_map[private["task_id"]]["independent_family_id"])
            for method in ("naive_rag", "verify_all"):
                peer = base[(condition, private["target_world"], private["task_id"], method)]
                row[f"action_equal_{method}"] = int(normalized_actions(record) == normalized_actions(peer))
                row[f"SR_delta_vs_{method}"] = int(record["metrics"]["SR"]) - int(peer["metrics"]["SR"])
            rows.append(row); records.append(record)
    return rows, records


def bootstrap(rows: list[dict[str, Any]], scope: str) -> dict[str, Any]:
    selected = [row for row in rows if row["scope"].startswith(scope)]
    unit_key = "task_id" if scope == "main" else "family_id"
    grouped: dict[str, list[float]] = defaultdict(list)
    for row in selected: grouped[str(row[unit_key])].append(float(row["SR_delta_vs_naive_rag"]))
    values = np.array([mean(v) for _, v in sorted(grouped.items())])
    rng = np.random.default_rng(SEED)
    draws = rng.choice(values, size=(10000, len(values)), replace=True).mean(axis=1)
    return {"scope": scope, "units": len(values), "SR_delta_vs_naive": float(values.mean()),
            "ci95_low": float(np.quantile(draws, .025)), "ci95_high": float(np.quantile(draws, .975)),
            "action_equal_naive": mean(r["action_equal_naive_rag"] for r in selected),
            "action_equal_verify_all": mean(r["action_equal_verify_all"] for r in selected)}


def evaluate() -> None:
    freeze = json.loads((OUT / "dev_freeze.json").read_text())
    if freeze["implementation_sha256"] != sha256(Path(__file__)):
        raise RuntimeError("CP implementation differs from dev freeze")
    pin, prior, vectors = setup()
    with tempfile.TemporaryDirectory(prefix="cp_eval_", dir="/root/autodl-tmp") as directory:
        temp = Path(directory)
        main_rows, main_records = evaluate_main(pin, prior, vectors, temp)
        ext_rows, ext_records = evaluate_ext(pin, prior, vectors, temp)
        cds_rows, cds_records = evaluate_cds(pin, prior, vectors, temp)
    rows = main_rows + ext_rows + cds_rows
    write_csv(OUT / "episode_metrics.csv", rows)
    effects = [bootstrap(rows, scope) for scope in ("main", "ext_v2", "cds")]
    write_csv(OUT / "paired_effects.csv", effects)
    with (OUT / "episodes.jsonl").open("w") as handle:
        for record in main_records + ext_records + cds_records:
            handle.write(json.dumps(record, sort_keys=True) + "\n")
    (OUT / "REPORT.md").write_text(
        "# CP-RAG diagnostic\n\nZero LLM calls. The implementation hash matched the dev8 freeze.\n\n" +
        "\n".join(f"- {r['scope']}: action agreement with Naive {r['action_equal_naive']:.3f}; "
                  f"SR delta {r['SR_delta_vs_naive']:.3f} "
                  f"[{r['ci95_low']:.3f}, {r['ci95_high']:.3f}]." for r in effects) + "\n"
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("dev", "evaluate"))
    args = parser.parse_args()
    develop() if args.phase == "dev" else evaluate()


if __name__ == "__main__": main()
