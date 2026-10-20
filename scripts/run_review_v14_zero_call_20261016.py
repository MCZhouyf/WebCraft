#!/usr/bin/env python3
"""Run W7b non-CP deterministic diagnostics registered on 2026-10-16."""
from __future__ import annotations

import csv
import argparse
import hashlib
import json
import os
import sys
import tempfile
from collections import Counter, defaultdict
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

ROOT = Path(__file__).resolve().parents[1]
ARB = Path(os.environ.get("W7B_ARBITER_ROOT", "/root/autodl-tmp/arbiter"))
ARB_DATA = Path("/root/autodl-tmp/arbiter")
sys.path[:0] = [str(ROOT), str(ARB / "src"), str(ARB / "scripts")]

from arbiter.evaluation.oracle_baseline import oracle_aggregation  # noqa: E402
from arbiter.evaluation.p6_execution import (  # noqa: E402
    _claims, _compiler, load_conflict_vectors, run_episode,
)
from arbiter.evaluation.p6_matrix import P6Job  # noqa: E402
from arbiter.pinning import build_pin, verify_webcraft_pin  # noqa: E402
import arbiter.adapters.claims as claims_adapter  # noqa: E402
import arbiter.adapters.search as search_adapter  # noqa: E402
import arbiter.adapters.webcraft as webcraft_adapter  # noqa: E402
import arbiter.evaluation.p6_execution as p6  # noqa: E402
import run_main  # type: ignore  # noqa: E402


OUT = ROOT / "reports/paperA/review_v14_zero_call"
ARB_MAIN = ARB_DATA / "artifacts/main/all_public"
INDEX = ROOT / "corpus/indexes/formal_p1"
SEED = 20261016
SOURCE_SEED = 20261006
METHODS = (
    "parametric", "probe_then_parametric", "naive_rag", "majority_rag",
    "truthfinder_rag", "verify_all", "arbiter_shell_naive", "arbiter",
    "oracle", "probe_then_rag",
)
PAIRS = {
    "old2new": ("mismatched_old2new", "matched_1.21.11", "1.21.11"),
    "new2old": ("mismatched_new2old", "matched_1.16.5", "1.16.5"),
}


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def write_csv(path: Path, rows: list[dict[str, Any]]) -> None:
    if not rows:
        raise RuntimeError(f"empty output: {path}")
    path.parent.mkdir(parents=True, exist_ok=True)
    fields: list[str] = []
    for row in rows:
        fields.extend(key for key in row if key not in fields)
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def setup_runtime() -> tuple[Path, dict[str, Any]]:
    OUT.mkdir(parents=True, exist_ok=True)
    pin = OUT / "webcraft_pin.json"
    pin.write_text(json.dumps(build_pin(ROOT), indent=2, sort_keys=True) + "\n")
    state = verify_webcraft_pin(pin, ROOT, require_ready=True)

    def cached(*args: Any, **kwargs: Any) -> dict[str, Any]:
        return state

    p6.verify_webcraft_pin = cached
    claims_adapter.verify_webcraft_pin = cached
    search_adapter.verify_webcraft_pin = cached
    webcraft_adapter.verify_webcraft_pin = cached

    class ForbiddenGateway:
        def __init__(self, *args: Any, **kwargs: Any):
            raise RuntimeError("W7b forbids LLM gateway construction")

    p6.GatewayClient = ForbiddenGateway
    prior, _ = run_main.load_prior_bundle(ARB_DATA / "artifacts/prior/frozen_prior_bundle.json")
    return pin, prior


def relation_key(operator: Any) -> tuple[str, str]:
    if operator.method == "mine":
        return ("drop", str(operator.params["node_type"]))
    if operator.method == "check_name":
        return ("name", str(operator.params["identifier"]))
    if operator.method == "smelt":
        return ("smelting", str(operator.output))
    return ("recipe", str(operator.output))


def operator_distance(left: Any, right: Any) -> tuple[int, str]:
    left_inputs = Counter(item for options in left.input_options for item in options)
    right_inputs = Counter(item for options in right.input_options for item in options)
    distance = sum((left_inputs - right_inputs).values()) + sum((right_inputs - left_inputs).values())
    return distance, right.mechanism_id


def raw_from_operator(operator: Any, chunk_id: str, suffix: str) -> dict[str, Any]:
    text = f"Deterministic target-world truth for {operator.method} {operator.output}."
    if operator.requires:
        text += " Requires " + ", ".join(item.replace("_", " ") for item in operator.requires) + "."
    if operator.method == "mine":
        claim_type, subject = "drop", str(operator.params["node_type"])
        payload = {"drops": [operator.output] * int(operator.output_count), "silk_touch": False}
    elif operator.method == "smelt":
        claim_type, subject = "smelting", str(operator.output)
        payload = {
            "ingredients": [operator.input_options[0][0]],
            "output_count": int(operator.output_count),
        }
    elif operator.method == "craft_grid":
        claim_type, subject = "recipe", str(operator.output)
        layout = list(operator.params["layout"])
        payload = {
            "ingredients": [item for item in layout if item], "layout": layout,
            "layout_options": [[item] if item else None for item in layout],
            "ordered": True, "output_count": int(operator.output_count),
            "station": "crafting_grid",
        }
    elif operator.method == "check_name":
        claim_type, subject = "name", str(operator.params["identifier"])
        payload = {"identifier": subject, "resolvable": True}
    else:
        raise ValueError(operator.method)
    return {
        "claim_id": "w7b_" + hashlib.sha256(f"{chunk_id}:{suffix}".encode()).hexdigest()[:20],
        "chunk_id": chunk_id, "claim_type": claim_type,
        "subject_item": subject, "payload": payload, "polarity": "positive",
        "tense": "current", "span_text": text,
        "extractor_version": "w7b-truth-v1",
    }


def claim_operators(bundle: dict[str, Any], raw: dict[str, Any], normalizer: Any) -> list[Any]:
    chunk = str(raw["chunk_id"])
    rows = [row for row in bundle["search_rows"] if str(row["chunk_id"]) == chunk]
    if not rows:
        return []
    _, operators = _claims({**bundle, "search_rows": rows, "claims": [raw]}, normalizer=normalizer)
    return operators


def oracle_ops(world: str, task: dict[str, Any]) -> list[Any]:
    initial = Counter({row["name"]: int(row["count"])
                       for row in task["standard_items"] + task.get("task_supplements", [])})
    operators, _ = oracle_aggregation(world, initial, ROOT)
    return operators


def path_ops(world: str, task: dict[str, Any], operators: list[Any]) -> list[Any]:
    plan = task["oracle_plans"].get(world)
    if not plan:
        return []
    selected = []
    for index, action in enumerate(plan["actions"]):
        method = action["method"]
        output = action.get("expected", {}).get("name")
        candidates = [op for op in operators if op.method == method and op.output == output]
        if method == "mine":
            candidates = [op for op in candidates if op.params.get("node_type") == action.get("node_type")]
        elif method == "smelt":
            candidates = [op for op in candidates if op.params.get("input") == action.get("input")]
        elif method == "craft_grid":
            layout = action.get("layout", [])
            exact = [op for op in candidates if op.params.get("layout") == layout]
            candidates = exact or candidates
        if not candidates:
            raise RuntimeError(f"no truth operator for {task['id']} {world} action {index}: {action}")
        selected.append(sorted(candidates, key=lambda op: op.mechanism_id)[0])
    return selected


def intervene(
    original: dict[str, Any], task: dict[str, Any], world: str, mode: str,
    normalizer: Any, vectors: dict[str, dict[str, bool]],
) -> tuple[dict[str, Any], dict[str, int]]:
    if mode == "O":
        return json.loads(json.dumps(original)), {"replaced": 0, "completed": 0, "no_valid_counterpart": 0}
    target_ops = oracle_ops(world, task)
    by_relation: dict[tuple[str, str], list[Any]] = defaultdict(list)
    for operator in target_ops:
        by_relation[relation_key(operator)].append(operator)
    claims = []
    replaced = unsupported = 0
    for raw in original["claims"]:
        compiled = claim_operators(original, raw, normalizer)
        stale = [op for op in compiled if op.mechanism_id in vectors
                 and not vectors[op.mechanism_id][world] and any(vectors[op.mechanism_id].values())]
        if mode in {"V", "VC"} and stale:
            options = by_relation.get(relation_key(stale[0]), [])
            if options:
                chosen = min(options, key=lambda op: operator_distance(stale[0], op))
                claims.append(raw_from_operator(chosen, str(raw["chunk_id"]), f"V:{raw['claim_id']}"))
                replaced += 1
                continue
            unsupported += 1
        claims.append(raw)
    rows = list(original["search_rows"])
    completed = 0
    if mode in {"C", "VC"}:
        provisional = {**original, "claims": claims, "search_rows": rows}
        present = {op.mechanism_id for op in _claims(provisional, normalizer=normalizer)[1]}
        for position, operator in enumerate(path_ops(world, task, target_ops)):
            if operator.mechanism_id in present:
                continue
            chunk = f"w7b_completion_{task['id']}_{world}_{position:03d}"
            condition = str(original["condition"])
            timestamp = "2021-02-13T16:05:32Z" if condition in {
                "matched_1.16.5", "mismatched_old2new"
            } else "2026-01-08T12:23:30Z"
            claim = raw_from_operator(operator, chunk, f"C:{operator.mechanism_id}")
            rows.append({
                "text": claim["span_text"], "source_site": "w7b_coverage_completion",
                "page_title": f"Coverage completion {operator.output}", "revid": chunk,
                "timestamp": timestamp, "chunk_id": chunk,
            })
            claims.append(claim)
            present.add(operator.mechanism_id)
            completed += 1
    reader = dict(original["reader"])
    reader["raw_claim_count"] = len(claims)
    reader["unique_chunk_count"] = len(rows)
    bundle = {**original, "claims": claims, "search_rows": rows, "reader": reader,
              "claim_source": f"w7b_{mode}", "reading_mode": "all_public"}
    return bundle, {"replaced": replaced, "completed": completed,
                    "no_valid_counterpart": unsupported}


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


def episode_metric(record: dict[str, Any], intervention: str,
                   pair: str, counts: dict[str, int]) -> dict[str, Any]:
    feasible = bool(record["true_feasible"])
    return {
        "pair": pair, "intervention": intervention, "condition": record["condition"],
        "world": record["world"], "task_id": record["task_id"],
        "method": record["method"], "terminal": terminal(record),
        "true_feasible": int(feasible), "SR": int(record["metrics"]["SR"]),
        "overconservative": int(feasible and record["reported_feasible"] is False),
        "execution_failure": int(feasible and record["reported_feasible"] is True
                                 and not record["metrics"]["SR"]),
        "plan_acceptance": int(record["metrics"]["HTR_commit"]),
        "semantic_cost": float(record["metrics"]["primary_cost"]),
        "search_calls": int(record["reader"].get("search_count", 0)),
        **counts,
    }


def paired_effects(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keyed = {(r["pair"], r["condition"], r["task_id"], r["method"], r["intervention"]): r
             for r in rows}
    rng = np.random.default_rng(SEED)
    output = []
    for pair, (mismatch, matched, _) in PAIRS.items():
        task_ids = sorted({r["task_id"] for r in rows if r["pair"] == pair})
        for method in METHODS:
            for cell_name, condition in (("mismatch", mismatch), ("matched", matched)):
                for metric in ("SR", "overconservative", "execution_failure", "plan_acceptance"):
                    vectors_by_effect = {}
                    base = np.array([float(keyed[(pair, condition, task, method, "O")][metric])
                                     for task in task_ids])
                    for effect, mode in (("dV", "V"), ("dC", "C"), ("dVC", "VC")):
                        value = np.array([float(keyed[(pair, condition, task, method, mode)][metric])
                                          for task in task_ids]) - base
                        vectors_by_effect[effect] = value
                    vectors_by_effect["interaction"] = (
                        vectors_by_effect["dVC"] - vectors_by_effect["dV"] - vectors_by_effect["dC"]
                    )
                    for effect, values in vectors_by_effect.items():
                        samples = rng.choice(values, size=(10000, len(values)), replace=True).mean(axis=1)
                        output.append({
                            "pair": pair, "cell": cell_name, "condition": condition,
                            "method": method, "metric": metric, "effect": effect,
                            "n_tasks": len(values), "estimate": float(values.mean()),
                            "ci95_low": float(np.quantile(samples, .025)),
                            "ci95_high": float(np.quantile(samples, .975)),
                            "bootstrap_draws": 10000, "seed": SEED,
                        })
    return output


def run_decomposition(pin: Path, prior: dict[str, Any]) -> None:
    tasks = run_main.load_evaluation_tasks(ROOT / "data/tasks.jsonl", ROOT / "config/resource_model.json")
    task_map = {row["id"]: row for row in tasks}
    raw_map = {row["id"]: row for row in jsonl(ROOT / "data/tasks.jsonl") if row["split"] == "eval"}
    vectors = load_conflict_vectors(ROOT / "data/conflicts.jsonl")
    normalizer, _ = _compiler(ROOT)
    baseline_rows = jsonl(ARB_DATA / "artifacts/main/all_public/episodes.jsonl")
    baseline_rows += jsonl(ARB_DATA / "artifacts/post_run_probe_then_rag/all_public/episodes.jsonl")
    baseline = {(r["condition"], r["world"], r["task_id"], r["method"]): r
                for r in baseline_rows if int(r["seed"]) == SOURCE_SEED}
    episode_rows, records = [], []
    with tempfile.TemporaryDirectory(prefix="w7b_decomp_", dir="/root/autodl-tmp") as directory:
        temp = Path(directory)
        for pair, (mismatch, matched, world) in PAIRS.items():
            for condition in (mismatch, matched):
                for task_id in sorted(task_map):
                    source = ARB_MAIN / "evidence" / f"{condition}__{task_id}__{SOURCE_SEED}.json"
                    original = json.loads(source.read_text())
                    for mode in ("O", "V", "C", "VC"):
                        bundle, counts = intervene(original, raw_map[task_id], world, mode,
                                                   normalizer, vectors)
                        for method in METHODS:
                            job = P6Job(condition, task_id, world, method, SEED)
                            record = run_episode(
                                job, task_map[task_id], bundle, root=ROOT, pin_path=pin,
                                index_dir=str(INDEX), output_dir=temp / f"{pair}_{mode}",
                                conflict_vectors=vectors, preview=False, prior_bundle=prior,
                            )
                            if any(event["cost"].get("model_calls", 0) for event in record["events"]):
                                raise RuntimeError("decomposition made a model call")
                            metric = episode_metric(record, mode, pair, counts)
                            episode_rows.append(metric)
                            records.append({
                                "pair": pair, "intervention": mode,
                                **{k: record[k] for k in (
                                    "condition", "world", "task_id", "method", "reported_feasible",
                                    "true_feasible", "used_mechanism_ids", "metrics", "reader",
                                )},
                            })
                            if mode == "O":
                                old = baseline[(condition, world, task_id, method)]
                                if (terminal(record), record["used_mechanism_ids"]) != (
                                    terminal(old), old["used_mechanism_ids"]
                                ):
                                    raise RuntimeError(
                                        f"O replay mismatch: {condition} {task_id} {method}; "
                                        f"new={(terminal(record), record['used_mechanism_ids'])}; "
                                        f"old={(terminal(old), old['used_mechanism_ids'])}"
                                    )
    write_csv(OUT / "decomposition_episode_metrics.csv", episode_rows)
    write_csv(OUT / "decomposition_effects.csv", paired_effects(episode_rows))
    with (OUT / "decomposition_episodes.jsonl").open("w") as handle:
        for row in records:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def run_cost_sensitivity() -> None:
    sources = []
    for mode in ("all_public", "llm_only"):
        rows = jsonl(ARB_DATA / f"artifacts/main/{mode}/episodes.jsonl")
        rows += jsonl(ARB_DATA / f"artifacts/post_run_probe_then_rag/{mode}/episodes.jsonl")
        sources.extend((mode, row) for row in rows if int(row["seed"]) == SOURCE_SEED)
    oracle = jsonl(ARB_DATA / "artifacts/post_review/oracle_reading/episodes.jsonl")
    sources.extend(("oracle_reading", row) for row in oracle)

    external_path = ARB_DATA / "reports/main/post_review/external_agents/rescore_v2/episode_metrics.csv"
    external = list(csv.DictReader(external_path.open())) if external_path.is_file() else []
    detail = []
    for mode, row in sources:
        searches = int(row.get("reader", {}).get("search_count", 0))
        base_cost = float(row["metrics"].get("primary_cost", 0))
        task = next(x for x in jsonl(ROOT / "data/tasks.jsonl") if x["id"] == row["task_id"])
        budget = float(task["action_budget"])
        for price in (.1, .25, .5):
            adjusted = base_cost + price * searches
            over = adjusted > budget
            detail.append({
                "scope": "pipeline", "reading_mode": mode, "method": row["method"],
                "task_id": row["task_id"], "condition": row["condition"], "world": row["world"],
                "search_cost": price, "search_calls": searches, "base_cost": base_cost,
                "budget": budget, "adjusted_cost": adjusted, "over_budget": int(over),
                "original_SR": int(row["metrics"]["SR"]),
                "rescored_SR": int(bool(row["metrics"]["SR"]) and not over),
            })
    # The corrected external rescore joins each canonical action log and exposes
    # measured search_count and primary_cost for the five external rows.
    for row in external:
        if row.get("source") != "external":
            continue
        searches = int(float(row.get("search_count", 0) or 0))
        base_cost = float(row.get("primary_cost", row.get("semantic_cost", 0)) or 0)
        budget = float(row.get("action_budget", 90) or 90)
        success = int(float(row.get("SR", row.get("success", 0)) or 0))
        for price in (.1, .25, .5):
            adjusted = base_cost + price * searches
            over = adjusted > budget
            detail.append({
                "scope": "external_static", "reading_mode": row.get("reading_mode", "external"),
                "method": row.get("method", "unknown"), "task_id": row.get("task_id", ""),
                "condition": row.get("condition", ""), "world": row.get("world", ""),
                "search_cost": price, "search_calls": searches, "base_cost": base_cost,
                "budget": budget, "adjusted_cost": adjusted, "over_budget": int(over),
                "original_SR": success, "rescored_SR": int(success and not over),
            })
    write_csv(OUT / "search_cost_episode_rescore.csv", detail)
    groups: dict[tuple[str, str, str, float], list[dict[str, Any]]] = defaultdict(list)
    for row in detail:
        groups[(row["scope"], row["reading_mode"], row["method"], row["search_cost"])].append(row)
    summary = []
    for key, rows in sorted(groups.items()):
        summary.append({
            **dict(zip(("scope", "reading_mode", "method", "search_cost"), key)),
            "episodes": len(rows), "over_budget": sum(r["over_budget"] for r in rows),
            "original_SR": mean(r["original_SR"] for r in rows),
            "rescored_SR": mean(r["rescored_SR"] for r in rows),
        })
    write_csv(OUT / "search_cost_sensitivity.csv", summary)


def run_cm() -> None:
    rows = list(csv.DictReader((ROOT / "reports/paperA/c_m_dv.csv").open()))
    mapping = {
        "name": "name", "recipe": "recipe", "crafting_recipe": "recipe",
        "concrete_grid_recipe": "recipe", "smelting": "smelting",
        "smelting_recipe": "smelting", "drop": "drop",
        "craftability": "craftability", "feasibility": "craftability",
    }
    grouped: dict[str, list[float]] = defaultdict(list)
    unknown = []
    for row in rows:
        raw = (row.get("decision_type") or row.get("assertion_kind") or "").lower()
        kind = mapping.get(raw)
        if kind is None:
            unknown.append((row["assertion_id"], raw))
        else:
            grouped[kind].append(float(row["c_m"]))
    if unknown:
        raise RuntimeError(f"unmapped c(m) rows: {unknown[:10]}")
    output = []
    for kind in ("name", "recipe", "smelting", "drop", "craftability"):
        values = np.array(grouped[kind], dtype=float)
        output.append({
            "type": kind, "n": len(values), "median": float(np.median(values)),
            "q1": float(np.quantile(values, .25, method="linear")),
            "q3": float(np.quantile(values, .75, method="linear")),
            "min": float(values.min()), "max": float(values.max()),
        })
    write_csv(OUT / "c_m_by_type.csv", output)


def run_reconstruction() -> None:
    from corpus.dump_pipeline import iter_pages, open_zst

    manifest = json.loads((ROOT / "corpus/manifests/fandom_history_snapshots.json").read_text())
    eligible = []
    for label, world in (("T1", "1.16.5"), ("T2", "1.19")):
        for title, row in manifest["snapshots"][world].items():
            eligible.append({"timepoint": label, "world": world, "page_title": title, **row})
    rng = np.random.default_rng(SEED)
    by_label = {label: [row for row in eligible if row["timepoint"] == label]
                for label in ("T1", "T2")}
    chosen = []
    for label in ("T1", "T2"):
        selected = rng.choice(len(by_label[label]), size=10, replace=False)
        chosen.extend(by_label[label][i] for i in sorted(selected))
    wanted = {int(row["revid"]) for row in chosen}
    indexed: dict[int, str] = {}
    dump = ROOT / "corpus/raw/internet_archive" / manifest["dump"]
    compressed, reader = open_zst(dump)
    try:
        for page in iter_pages(reader):
            for revision in page.revisions:
                if revision.revid in wanted:
                    indexed[revision.revid] = revision.text
            if len(indexed) == len(wanted):
                break
    finally:
        reader.close(); compressed.close()
    audit = []
    for row in chosen:
        revid = int(row["revid"])
        source = indexed.get(revid)
        local = ROOT / "corpus/raw/fandom_history" / row["wikitext"]
        local_text = local.read_text() if local.is_file() else None
        match = source is not None and local_text is not None and source == local_text
        audit.append({
            "timepoint": row["timepoint"], "page_title": row["page_title"], "revid": revid,
            "dump_file": str(dump.relative_to(ROOT)), "local_snapshot": str(local.relative_to(ROOT)),
            "found_in_public_dump": int(source is not None),
            "snapshot_text_exact": int(match),
            "parser_status": "ok" if match else "missing_or_text_mismatch",
            "text_bytes": len(source.encode()) if source else 0,
            "wiki_http_requests": 0,
        })
    write_csv(OUT / "reconstruction_20_revid.csv", audit)


def write_report() -> None:
    effects = list(csv.DictReader((OUT / "decomposition_effects.csv").open()))
    primary = [row for row in effects if row["pair"] == "old2new" and row["cell"] == "mismatch"
               and row["method"] == "naive_rag" and row["metric"] == "SR"]
    cm = list(csv.DictReader((OUT / "c_m_by_type.csv").open()))
    lines = [
        "# W7b zero-call diagnostics", "",
        "> Diagnostic counterfactuals; no frozen result was changed and no LLM was called.", "",
        "## Validity x coverage", "",
        "Primary old-to-new mismatch, Naive SR:", "",
        "| Effect | Estimate | 95% task bootstrap interval |", "|---|---:|---:|",
    ]
    for row in primary:
        lines.append(f"| {row['effect']} | {float(row['estimate']):.4f} | "
                     f"[{float(row['ci95_low']):.4f}, {float(row['ci95_high']):.4f}] |")
    lines += ["", "Effects are not normalized to sum to the frozen 11.7647 pp gap; the interaction is reported explicitly.",
              "", "## c(m) by type", "", "| Type | n | median | Q1-Q3 | range |", "|---|---:|---:|---:|---:|"]
    for row in cm:
        lines.append(f"| {row['type']} | {row['n']} | {float(row['median']):g} | "
                     f"{float(row['q1']):g}-{float(row['q3']):g} | "
                     f"{float(row['min']):g}-{float(row['max']):g} |")
    lines += ["", "Search-cost sensitivity is a static rescore; external policies did not adapt to price.",
              "The reconstruction sample made zero programmatic Wiki requests."]
    (OUT / "REPORT.md").write_text("\n".join(lines) + "\n")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--support-only", action="store_true")
    args = parser.parse_args()
    pin, prior = setup_runtime()
    if not args.support_only:
        run_decomposition(pin, prior)
    run_cost_sensitivity()
    run_cm()
    run_reconstruction()
    if not args.support_only:
        write_report()
    print(json.dumps({"status": "ok", "output": str(OUT)}, sort_keys=True))


if __name__ == "__main__":
    main()
