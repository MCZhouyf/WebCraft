"""Rebuild audit exports from accepted logs only; never run an agent."""
from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import resource
import subprocess
from collections import Counter, defaultdict
from pathlib import Path

from arbiter.evaluation.external_rescore import (
    TERMINALS, action_audit, clustered_ratio, outcome, paired_difference,
)
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes
from export_external_baselines import CELLS, FROZEN_METHODS


ROOT = Path(__file__).resolve().parents[1]
RUNS = ("direct_llm_assembled", "react_assembled", "reflexion_assembled", "adam", "xenon_assembled")
METRICS = {
    "SR": ("SR", None), "feasible_task_SR": ("feasible_success", "true_feasible"),
    "false_abstention_rate": ("false_abstention", "true_feasible"),
    "legacy_HTR_all": ("legacy_HTR_all", None),
    "legacy_self_reported_HTR_commit": ("legacy_self_reported_HTR_commit", None),
    "behavioral_commit_upper": ("behavioral_HTR_commit_upper", None),
    "any_action_failure_rate": ("any_action_failure", None),
    "action_failure_rate": ("failed_actions", "semantic_actions"),
    "terminal_execution_failure_rate": ("terminal_execution_failure", None),
    "mean_world_probe_count": ("world_probe_count", None),
    "mean_probe_cost": ("probe_cost", None),
    "mean_model_calls": ("model_calls", None),
    "mean_input_tokens": ("input_tokens", None), "mean_output_tokens": ("output_tokens", None),
}


def sha(path):
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def jsonl(path):
    with path.open() as handle:
        for line in handle:
            if line.strip():
                yield json.loads(line)


def write_csv(path, rows):
    if not rows:
        raise ValueError(f"empty output {path}")
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, list(rows[0]), lineterminator="\n")
        writer.writeheader()
        for row in rows:
            writer.writerow({k: json.dumps(v, sort_keys=True) if isinstance(v, (list, dict)) else v
                             for k, v in row.items()})


def markdown(rows):
    fields = list(rows[0])
    lines = ["| " + " | ".join(fields) + " |", "|" + "---|" * len(fields)]
    for row in rows:
        values = ["NA" if row[k] is None else f"{row[k]:.4f}" if isinstance(row[k], float)
                  else str(row[k]) for k in fields]
        lines.append("| " + " | ".join(values) + " |")
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--webcraft", type=Path, default=Path("/root/autodl-tmp/webcraft"))
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "reports/main/post_review/external_agents/rescore_v2")
    args = parser.parse_args()
    available = int(next(line.split()[1] for line in Path("/proc/meminfo").read_text().splitlines()
                         if line.startswith("MemAvailable:"))) * 1024
    cg_max = Path("/sys/fs/cgroup/memory.max")
    if cg_max.exists() and cg_max.read_text().strip() != "max":
        available = min(available, int(cg_max.read_text()) -
                        int(Path("/sys/fs/cgroup/memory.current").read_text()))
    if available < 1.8 * 1024**3:
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")
    out = args.output_dir
    if out.resolve() == (ROOT / "reports/main/post_review/external_agents").resolve():
        raise ValueError("refusing to overwrite legacy exports")
    out.mkdir(parents=True, exist_ok=True)
    sources = {}

    def track(path):
        path = path.resolve()
        if str(path) not in sources:
            sources[str(path)] = sha(path)
        return path

    task_path = track(args.webcraft / "data/tasks.jsonl")
    conflict_path = track(args.webcraft / "data/conflicts.jsonl")
    cost_path = track(args.webcraft / "config/cost_model.json")
    arena_path = track(args.webcraft / "harness/config/arena.json")
    pin_path = track(ROOT / "config/webcraft_pin.json")
    pin = json.loads(pin_path.read_text())
    pinned = {k: v for root in pin["roots"].values() for k, v in root.get("files", {}).items()}
    for path in (task_path, conflict_path, cost_path):
        relative = str(path.relative_to(args.webcraft))
        if pinned.get(relative) != sha(path):
            raise ValueError(f"gold input differs from pinned hash: {relative}")
    cost_model = json.loads(cost_path.read_text())
    if cost_model["charges"] != {
        "mine": 1, "craft_grid": 1, "smelt": "requested_positive_integer_n", "check_name": 1,
        "inventory": 0, "report_version": 0, "submit": 0, "search": 0,
    } or not cost_model["failed_actions_charged"]:
        raise ValueError("cost model changed")
    tasks = {t["id"]: t for t in jsonl(task_path)}
    conflicts = load_conflicts(conflict_path)
    nodes = load_resource_nodes(arena_path)
    for path in (ROOT / "src/arbiter/evaluation/external_agents.py",
                 ROOT / "src/arbiter/evaluation/external_scoring.py",
                 ROOT / "src/arbiter/evaluation/external_rescore.py", Path(__file__),
                 ROOT / "reports/main/post_review/external_agents/summary.csv",
                 ROOT / "reports/main/post_review/external_agents/episode_metrics.csv"):
        track(path)
    runs = ROOT / "artifacts/post_review/external_agents/final"
    external = [r for directory in RUNS for r in jsonl(track(runs / directory / "episodes.jsonl"))]
    task_ids = {r["task_id"] for r in external}
    if len(task_ids) != 22:
        raise ValueError("expected 22 task clusters")
    expected = {(t, c, w) for t in task_ids for c, w in CELLS}
    gold = []
    for tid, condition, world in sorted(expected):
        task = tasks[tid]
        feasible = task["feasible"][world]
        plan = task["oracle_plans"][world]
        if feasible != (task["oracle_cost"][world] is not None) or feasible != bool(plan):
            raise ValueError("oracle metadata disagrees with gold feasibility")
        if feasible and not plan["feasible"]:
            raise ValueError("gold plan not feasible")
        gold.append({"task_id": tid, "condition": condition, "world": world,
                     "true_feasible": int(feasible), "oracle_cost": task["oracle_cost"][world],
                     "oracle_plan_present": bool(plan), "goal": task["target"]})
    main_path = track(ROOT / "artifacts/main/all_public/episodes.jsonl")
    frozen = (r for r in jsonl(main_path) if r["method"] in FROZEN_METHODS
              and r["seed"] == 20261006 and (r["condition"], r["world"]) in CELLS
              and r["task_id"] in task_ids)
    rows, events_out, costs_out, fairness = [], [], [], []
    seen = defaultdict(set)
    manifests = {}

    def process(row, external_run):
        key = (row["task_id"], row["condition"], row["world"])
        if key in seen[row["method"]] or key not in expected or row["seed"] != 20261006:
            raise ValueError("duplicate or unregistered episode")
        seen[row["method"]].add(key)
        identity = {k: row[k] for k in ("method", "task_id", "condition", "world", "seed")}
        if external_run:
            action_path = track(ROOT / row["action_log"])
            events = list(jsonl(action_path))
            manifest_path = track(action_path.parent.parent / "run_manifest.json")
            manifest = manifests.setdefault(str(manifest_path), json.loads(manifest_path.read_text()))
            row["_max_calls"] = manifest["max_model_calls"]
            row["_max_actions"] = manifest["max_actions"]
            fairness.append({**identity, "model": row["model"],
                             "returned_models": sorted({d.get("model") or "missing" for d in row["diagnostics"]}),
                             **{k: manifest[k] for k in ("code_commit", "pin_sha256", "max_model_calls",
                                                        "max_actions", "thinking", "reading_mode")},
                             "trace_retention_steps": 2 if row["method"] == "direct_llm" else 8,
                             "intent_overwritten": row["method"] != "adam",
                             "action_log": str(action_path), "manifest": str(manifest_path)})
        else:
            events = row["events"]
        result = outcome(row, events, tasks[row["task_id"]])
        audited, event_rows = action_audit(row, events, conflicts, nodes)
        diagnostics = row.get("diagnostics", [])
        result.update(audited)
        result.update({"model_calls": row.get("model_calls", 0),
                       "input_tokens": row.get("input_tokens", 0),
                       "output_tokens": row.get("output_tokens", 0),
                       "max_single_call_input_tokens": max((d.get("input_tokens", 0) for d in diagnostics), default=0),
                       "truncated_completions": sum(d.get("finish_reason") == "length" for d in diagnostics),
                       "invalid_model_outputs": sum(bool(d.get("error_code")) and
                                                    d["error_code"] != "action_budget_exceeded" for d in diagnostics),
                       "model": row.get("model", "no_episode_LLM"),
                       "reader_search_count": row.get("reader", {}).get("search_count", audited["search_count"])})
        rows.append({**identity, "source": "external" if external_run else "frozen", **result})
        events_out.extend({**identity, "episode_method": identity["method"], **e} for e in event_rows)
        by_tool = defaultdict(list)
        for event in events:
            if event.get("intent") == "probe":
                by_tool[event["method"]].append(event)
        for tool, group in sorted(by_tool.items()):
            costs_out.append({**identity, "tool": tool, "labelled_probes": len(group),
                              "primary_cost": sum(e["cost"]["primary"] for e in group)})

    for row in frozen:
        process(row, False)
    for row in external:
        process(row, True)
    if len(seen) != 12 or any(keys != expected for keys in seen.values()):
        raise ValueError("incomplete 12-method paired matrix")
    if any(r["SR"] != r["original_SR"] or r["cost_mismatches"] for r in rows):
        raise ValueError("SR or cost mismatch: audit before exporting")
    groups = {m: [r for r in rows if r["method"] == m] for m in sorted(seen)}
    summary, intervals, terminals, cells = [], [], [], []
    for method, group in groups.items():
        n = len(group)
        feasible = sum(r["true_feasible"] for r in group)
        summary.append({"method": method, "episodes": n, "feasible": feasible, "infeasible": n-feasible,
                        "successes": sum(r["SR"] for r in group),
                        "feasible_successes": sum(r["feasible_success"] for r in group),
                        "correct_infeasible": sum(r["correct_infeasible"] for r in group),
                        "false_abstentions": sum(r["false_abstention"] for r in group),
                        "SR": sum(r["SR"] for r in group)/n,
                        "feasible_task_SR": sum(r["feasible_success"] for r in group)/feasible,
                        "false_abstention_rate": sum(r["false_abstention"] for r in group)/feasible,
                        "behavioral_HTR_commit": None if any(r["behavioral_unknown"] for r in group) else 0,
                        "behavioral_unknown_episodes": sum(r["behavioral_unknown"] for r in group),
                        "behavioral_lower": 0,
                        "behavioral_upper": sum(r["behavioral_HTR_commit_upper"] for r in group)/n,
                        "legacy_HTR_all": sum(r["legacy_HTR_all"] for r in group)/n,
                        "self_reported_HTR_commit": sum(r["legacy_self_reported_HTR_commit"] for r in group)/n,
                        "any_failure_episodes": sum(r["any_action_failure"] for r in group),
                        "labelled_probe_count": sum(r["labelled_probe_count"] for r in group),
                        "world_probe_count": sum(r["world_probe_count"] for r in group),
                        "probe_cost": sum(r["probe_cost"] for r in group),
                        "mean_search_count": sum(r["reader_search_count"] for r in group)/n,
                        "call_cap_hit_episodes": sum(r["call_cap_hit"] for r in group),
                        "action_cap_hit_episodes": sum(r["action_cap_hit"] for r in group),
                        "truncated_completions": sum(r["truncated_completions"] for r in group),
                        "mean_calls": sum(r["model_calls"] for r in group)/n,
                        "input_tokens": sum(r["input_tokens"] for r in group),
                        "output_tokens": sum(r["output_tokens"] for r in group),
                        "max_single_call_input_tokens": max(r["max_single_call_input_tokens"] for r in group)})
        for name, (num, den) in METRICS.items():
            intervals.append({"method": method, "metric": name, **clustered_ratio(group, num, den)})
        terminals.append({"method": method, **{t: sum(r["terminal"] == t for r in group) for t in TERMINALS}})
        for condition, world in sorted(CELLS):
            cell = [r for r in group if r["condition"] == condition and r["world"] == world]
            f = sum(r["true_feasible"] for r in cell)
            cells.append({"method": method, "condition": condition, "world": world, "n": len(cell),
                          "feasible": f, "SR": sum(r["SR"] for r in cell)/len(cell),
                          "feasible_task_SR": sum(r["feasible_success"] for r in cell)/f,
                          "correct_infeasible": sum(r["correct_infeasible"] for r in cell),
                          "false_abstention_rate": sum(r["false_abstention"] for r in cell)/f,
                          "legacy_HTR_all": sum(r["legacy_HTR_all"] for r in cell)/len(cell)})
    pairs = []
    for method in groups:
        if method != "naive_rag":
            for metric in ("SR", "legacy_HTR_all"):
                pairs.append({"comparison": method + " - naive_rag", "metric": metric,
                              **paired_difference(groups[method], groups["naive_rag"], metric)})
            pairs.append({"comparison": method + " - naive_rag", "metric": "feasible_task_SR",
                          **paired_difference(groups[method], groups["naive_rag"], "SR", feasible_only=True)})
        for mismatch, world in (("mismatched_old2new", "1.21.11"), ("mismatched_new2old", "1.16.5")):
            a = [r for r in groups[method] if r["condition"] == mismatch]
            b = [r for r in groups[method] if r["condition"] == "matched_" + world]
            for metric in ("SR", "legacy_HTR_all", "feasible_task_SR"):
                pairs.append({"comparison": method + ": " + mismatch + " - matched",
                              "metric": metric, **paired_difference(a, b, "SR" if metric == "feasible_task_SR" else metric,
                                                                       within_world=True, feasible_only=metric == "feasible_task_SR")})
    abstain = [{"task_id": r["task_id"], "SR": 1-r["true_feasible"]} for r in gold]
    baseline = {"method": "always_abstain_analytic", "episodes": len(gold),
                "feasible_task_SR": 0, "false_abstention_rate": 1,
                **clustered_ratio(abstain, "SR")}
    historical = []
    for commit in sorted({r["code_commit"] for r in fairness}):
        source = subprocess.check_output(
            ["git", "show", commit + ":src/arbiter/evaluation/external_agents.py"], cwd=ROOT, text=True)
        functions = {n.name: hashlib.sha256(ast.dump(n, include_attributes=False).encode()).hexdigest()
                     for n in ast.parse(source).body if isinstance(n, ast.FunctionDef)
                     and n.name in {"public_system_prompt", "_execute", "_public_task", "run_external_episode",
                                    "_parse", "_validate_args"}}
        historical.append({"code_commit": commit, **functions})
    main_table = []
    for method, group in groups.items():
        def ci(metric):
            return next(r for r in intervals if r["method"] == method and r["metric"] == metric)
        sr, feasible_ci = ci("SR"), ci("feasible_task_SR")
        infeasible = sum(not r["true_feasible"] for r in group)
        main_table.append({"method": method, "episodes": len(group),
                           "successes": sum(r["SR"] for r in group),
                           "SR": sr["estimate"], "SR_low": sr["low"], "SR_high": sr["high"],
                           "feasible_successes": sum(r["feasible_success"] for r in group),
                           "feasible_n": len(group)-infeasible,
                           "feasible_task_SR": feasible_ci["estimate"],
                           "feasible_SR_low": feasible_ci["low"], "feasible_SR_high": feasible_ci["high"],
                           "correct_infeasible": sum(r["correct_infeasible"] for r in group),
                           "infeasible_n": infeasible,
                           "correct_infeasible_rate": sum(r["correct_infeasible"] for r in group)/infeasible,
                           "false_abstention_rate": ci("false_abstention_rate")["estimate"],
                           "behavioral_HTR_commit": None if any(r["behavioral_unknown"] for r in group) else 0})
    main_table.append({"method": "always_abstain_analytic", "episodes": len(gold),
                       "successes": sum(not r["true_feasible"] for r in gold),
                       "SR": baseline["estimate"], "SR_low": baseline["low"], "SR_high": baseline["high"],
                       "feasible_successes": 0, "feasible_n": sum(r["true_feasible"] for r in gold),
                       "feasible_task_SR": 0, "feasible_SR_low": 0, "feasible_SR_high": 0,
                       "correct_infeasible": sum(not r["true_feasible"] for r in gold),
                       "infeasible_n": sum(not r["true_feasible"] for r in gold),
                       "correct_infeasible_rate": 1, "false_abstention_rate": 1, "behavioral_HTR_commit": 0})
    audit_summary = {
        "gold_feasible": sum(r["true_feasible"] for r in gold),
        "gold_infeasible": sum(1-r["true_feasible"] for r in gold),
        "episodes": len(rows), "external_episodes": len(external),
        "original_SR_mismatches": sum(r["SR"] != r["original_SR"] for r in rows),
        "external_success_with_any_action_failure": sum(r["SR"] and r["any_action_failure"] for r in rows if r["source"] == "external"),
        "cost_mismatches": sum(r["cost_mismatches"] for r in rows),
        "legacy_HTR_all_positive": sum(r["legacy_HTR_all"] for r in rows),
        "contact_vs_legacy_disagreements": sum(r["catalogue_contact_upper"] != r["legacy_HTR_all"] for r in rows),
        "goal_attained_without_success": sum(r["goal_attained"] and not r["SR"] for r in rows),
        "always_abstain": baseline,
        "behavioral_status": "not_point_identified; intervals are identification bounds, not confidence intervals",
        "model_labels": {m: dict(Counter(r["model"] for r in g)) for m, g in groups.items()},
        "manifest_limits": {k: sorted({r[k] for r in fairness}) for k in
                            ("max_model_calls", "max_actions", "thinking", "pin_sha256", "reading_mode")},
        "new_LLM_calls": 0, "new_simulator_episodes": 0,
        "effective_available_memory_before_bytes": available,
        "historical_agent_function_variants": len({tuple(v for k, v in r.items() if k != "code_commit") for r in historical}),
        "peak_RSS_KiB": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    tables = {"gold_feasibility": gold, "episode_metrics": rows, "summary": summary,
              "cluster_intervals": intervals, "paired_differences": pairs,
              "terminal_distribution": terminals, "condition_metrics": cells,
              "action_audit": events_out, "probe_cost_audit": costs_out, "fairness": fairness}
    tables.update({"main_table": main_table, "historical_implementation_audit": historical})
    for name, table in tables.items():
        write_csv(out / (name + ".csv"), table)
    for name in ("summary", "main_table", "terminal_distribution", "condition_metrics", "paired_differences"):
        (out / (name + ".md")).write_text(markdown(tables[name]))
    (out / "audit_summary.json").write_text(json.dumps(audit_summary, indent=2, sort_keys=True) + "\n")
    for path, before in sources.items():
        if sha(Path(path)) != before:
            raise RuntimeError(f"input changed during rescore: {path}")
    provenance = {"webcraft_pinned_commit": pin["webcraft_commit"],
                  "rescore_parent_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
                  "input_sha256": sources, "all_inputs_unchanged_after_run": True}
    (out / "provenance.json").write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit_summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
