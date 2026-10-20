"""Run post-review external LLM agents against the authorized 22-task subset."""
from __future__ import annotations

import argparse
import json
import resource
import time
import hashlib
import subprocess
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter, load_webcraft_module
from arbiter.checkpoint import memory_requirement, require_memory, effective_available_bytes
from arbiter.evaluation.external_agents import (
    EXTERNAL_METHODS, run_external_episode,
)
from arbiter.evaluation.external_scoring import (
    load_conflicts, load_resource_nodes, score_external_trace,
)
from arbiter.llm import GatewayClient, GatewayError
from arbiter.pinning import verify_webcraft_pin

from run_main import ROOT, WEBCRAFT, load_evaluation_tasks


CELLS = (
    ("matched_1.16.5", "1.16.5"), ("matched_1.19", "1.19"),
    ("matched_1.21.11", "1.21.11"), ("mismatched_old2new", "1.21.11"),
    ("mismatched_new2old", "1.16.5"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", choices=EXTERNAL_METHODS, nargs="+", required=True)
    parser.add_argument("--limit", type=int, default=0, help="stable prefix job limit; 0 is the full matrix")
    parser.add_argument("--offset", type=int, default=0, help="skip this fixed prefix before applying --limit")
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--thinking", choices=("enabled", "disabled"), default="disabled")
    parser.add_argument("--request-interval", type=float, default=10.0)
    parser.add_argument("--planned-memory-mib", type=int, default=512)
    parser.add_argument("--ledger", type=Path, default=Path("logs/post_review/external_agents_ledger.jsonl"))
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/post_review/external_agents"))
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--index-dir", type=Path, default=WEBCRAFT / "corpus/indexes/formal_p1")
    return parser.parse_args()


def _jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def _goal_count(inventory: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return sum(int(row["count"]) for row in inventory if row["name"] == target["item"])


def main() -> int:
    args = parse_args()
    if args.limit < 0 or args.offset < 0:
        raise ValueError("limit and offset must be nonnegative")
    if not 0 <= args.request_interval <= 60:
        raise ValueError("request interval must be between 0 and 60 seconds")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise RuntimeError("output directory must be empty; existing diagnostic runs are preserved")
    if args.planned_memory_mib <= 0:
        raise ValueError("planned memory must be positive")
    planned_memory = args.planned_memory_mib * 1024**2
    required_memory = memory_requirement(planned_memory, 2 * 1024**3)
    while effective_available_bytes() < required_memory:
        print(json.dumps({"status": "waiting_for_memory", "poll_seconds": 600,
                          "required_bytes": required_memory}), flush=True)
        for _ in range(10):
            time.sleep(60)
    available_before = require_memory(planned_memory, reserve_bytes=2 * 1024**3)
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    started = time.monotonic()
    root = args.webcraft_root.resolve()
    verify_webcraft_pin(ROOT / "config/webcraft_pin.json", root, require_ready=True)
    tasks = load_evaluation_tasks(root / "data/tasks.jsonl", root / "config/resource_model.json")
    selected = [task for task in tasks if task["conflict_ids"]]
    if len(selected) != 22:
        raise RuntimeError(f"post-review subset must contain 22 conflict-bearing tasks, got {len(selected)}")
    jobs = [
        (method, condition, world, task)
        for method in args.methods for condition, world in CELLS for task in selected
    ]
    jobs.sort(key=lambda row: (row[0], row[1], row[2], row[3]["id"]))
    jobs = jobs[args.offset:]
    if args.limit:
        jobs = jobs[:args.limit]
    if not jobs:
        raise ValueError("no jobs selected")
    client = GatewayClient(args.ledger)
    client.min_interval_seconds = args.request_interval
    args.output_dir.mkdir(parents=True, exist_ok=True)
    try:
        client.validate_canary(thinking=args.thinking)
    except GatewayError as exc:
        status = {"status": "stopped_preflight", "error_type": type(exc).__name__,
                  "episodes_created": 0}
        (args.output_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
        return 2
    if args.preflight_only:
        status = {"status": "preflight_passed", "episodes_created": 0, "model": client.model}
        (args.output_dir / "status.json").write_text(json.dumps(status, indent=2) + "\n")
        print(json.dumps(status), flush=True)
        return 0
    sim = load_webcraft_module("sim", root=root, pin_path=ROOT / "config/webcraft_pin.json")
    conflicts = load_conflicts(root / "data/conflicts.jsonl")
    resource_nodes = load_resource_nodes(root / "harness/config/arena.json")
    manifest = {
        "schema_version": 1, "started_at": datetime.now(timezone.utc).isoformat(),
        "seed": 20261006, "methods": args.methods, "cells": [list(cell) for cell in CELLS],
        "selected_task_ids": [task["id"] for task in selected], "episodes": len(jobs),
        "max_model_calls": 40, "max_actions": 90, "model": client.model,
        "currency_cost_status": "unknown_unpriced", "reading_mode": "all_public",
        "preflight_canary": "passed",
        "thinking": args.thinking,
        "request_interval_seconds": args.request_interval,
        "planned_memory_mib": args.planned_memory_mib,
        "required_memory_bytes": required_memory,
        "job_offset": args.offset,
        "credential_count": getattr(client, "credential_count", 1),
        "credential_policy": "429_only_switch_then_canary; quarantine_limited_key_for_run",
        "ledger": str(args.ledger),
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "pin_sha256": hashlib.sha256((ROOT / "config/webcraft_pin.json").read_bytes()).hexdigest(),
        "jobs": [{"method": m, "condition": c, "world": w, "task_id": t["id"]} for m, c, w, t in jobs],
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    rows_path = args.output_dir / "episodes.jsonl"
    failures: Counter[str] = Counter()
    completed = 0
    stop_reason = None
    for method, condition, world, task in jobs:
        initial = Counter({row["name"]: int(row["count"]) for row in task["standard_items"] + task.get("task_supplements", [])})
        action_path = args.output_dir / "actions" / f"{method}__{condition}__{world}__{task['id']}.jsonl"
        handle = sim.create_simulated_agent(
            version=world, log_path=action_path, initial_inventory=initial,
            search_condition=condition, search_index_dir=str(args.index_dir / condition),
        )
        api = PublicSemanticAdapter(handle["api"])
        try:
            episode = run_external_episode(api, client, method=method, task=task, thinking=args.thinking)
            final_inventory = api.inventory(intent="commit")
            handle["evaluator"].finish_episode()
        except GatewayError as exc:
            stop_reason = type(exc).__name__
            _jsonl(args.output_dir / "aborted.jsonl", {
                "method": method, "condition": condition, "world": world,
                "task_id": task["id"], "error_type": stop_reason,
                "action_log": str(action_path), "included_in_metrics": False,
            })
            break
        finally:
            handle["shutdown"]()
        if episode.model_output_error:
            stop_reason = episode.model_output_error
            _jsonl(args.output_dir / "aborted.jsonl", {
                "method": method, "condition": condition, "world": world,
                "task_id": task["id"], "error_type": "model_output_error",
                "error_code": stop_reason, "diagnostics": episode.diagnostics,
                "action_log": str(action_path), "included_in_metrics": False,
            })
            break
        true_feasible = bool(task["feasible"][world])
        produced = _goal_count(final_inventory, task["target"]) >= int(task["target"]["count"])
        success = bool((true_feasible and episode.reported_feasible is True and produced)
                       or (not true_feasible and episode.reported_feasible is False))
        events = [
            json.loads(line) for line in action_path.read_text().splitlines() if line.strip()
        ]
        metrics = score_external_trace(
            events, world=world, conflicts=conflicts, resource_nodes=resource_nodes,
            true_feasible=true_feasible, reported_feasible=episode.reported_feasible,
        )
        metrics["SR"] = int(success)
        terminal = "infrastructure_error" if episode.infrastructure_error else (
            "model_output_error" if episode.model_output_error else
            "solved" if success else "execution_or_planning_failure"
        )
        if not success:
            failures[method] += 1
        _jsonl(rows_path, {
            "schema_version": 1, "record_type": "external_episode", "method": method,
            "condition": condition, "world": world, "task_id": task["id"], "seed": 20261006,
            "reading_mode": "all_public", "model": client.model,
            "reported_feasible": episode.reported_feasible, "success": int(success),
            "terminal": terminal, "model_calls": episode.model_calls,
            "input_tokens": episode.input_tokens, "output_tokens": episode.output_tokens,
            "tool_actions": episode.tool_actions, "infrastructure_error": episode.infrastructure_error,
            "model_output_error": episode.model_output_error,
            "diagnostics": episode.diagnostics, "primary_cost": episode.primary_cost,
            "true_feasible": true_feasible, "metrics": metrics,
            "action_log": str(action_path),
        })
        completed += 1
        print(json.dumps({"completed": completed, "requested": len(jobs), "task_id": task["id"],
                          "calls": episode.model_calls, "success": int(success)}), flush=True)
    summary = {
        "status": "stopped" if stop_reason else "completed", "stop_reason": stop_reason,
        "episodes_requested": len(jobs), "episodes_completed": completed,
        "failures": dict(failures), "output": str(args.output_dir),
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_before_kib": rss_before,
        "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "available_before_bytes": available_before,
        "available_after_bytes": effective_available_bytes(),
    }
    (args.output_dir / "status.json").write_text(json.dumps(summary, indent=2) + "\n")
    print(json.dumps(summary, sort_keys=True), flush=True)
    return 2 if stop_reason else 0


if __name__ == "__main__":
    raise SystemExit(main())
