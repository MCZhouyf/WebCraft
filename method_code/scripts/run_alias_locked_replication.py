"""Run the preregistered alias-locked subset without evaluator-side inputs."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import resource
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter
from arbiter.checkpoint import effective_available_bytes, memory_requirement, require_memory
from arbiter.evaluation.external_agents import public_system_prompt, run_external_episode
from arbiter.llm.gateway import BudgetExceeded, CanaryFailed, GatewayError, GatewayProtocolError
from arbiter.llm.openai_probe import OpenAIProbeClient, ProbeRefusal


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
MODEL = "glm-5.2"
SELECTION = ROOT / "prereg/alias_locked_sample_20261011.json"
PREREG = ROOT / "prereg/alias_locked_replication_20261011.md"
EXPECTED = {"react": 47, "reflexion": 10}
PROMPT_HASHES = {
    "react": "5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84",
    "reflexion": "e2ea63c01a48323f49bb29fb21a746184ae34641cdc2373012b774f858223a6e",
}


def verify_frozen_files(root: Path) -> tuple[dict[str, Any], str]:
    pin = json.loads((ROOT / "config/webcraft_pin.json").read_text())
    mismatches = []
    for group in pin["roots"].values():
        for relative, expected in group.get("files", {}).items():
            path = root / relative
            actual = hashlib.sha256(path.read_bytes()).hexdigest() if path.is_file() else None
            if actual != expected:
                mismatches.append(relative)
    if mismatches:
        raise RuntimeError(f"frozen WebCraft file mismatch: {mismatches}")
    current = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return pin, current


def load_frozen_sim(root: Path):
    resolved = str(root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    return importlib.import_module("sim")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--methods", choices=tuple(EXPECTED), nargs="+", required=True)
    parser.add_argument("--selection", type=Path, default=SELECTION)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--index-dir", type=Path, default=WEBCRAFT / "corpus/indexes/formal_p1")
    parser.add_argument("--request-interval", type=float, default=10.0)
    parser.add_argument("--planned-memory-mib", type=int, default=160)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def retryable_gateway_failure(exc: GatewayError, ledger: Path) -> bool:
    if isinstance(exc, (BudgetExceeded, CanaryFailed, GatewayProtocolError, ProbeRefusal)):
        return False
    if not ledger.is_file():
        return False
    rows = [json.loads(line) for line in ledger.read_text().splitlines() if line]
    if not rows:
        return False
    latest = rows[-1]
    status = latest.get("http_status")
    if status in {408, 429} or isinstance(status, int) and 500 <= status <= 599:
        return True
    return latest.get("error_type") in {
        "URLError", "TimeoutError", "RemoteDisconnected", "IncompleteRead",
        "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError",
    }


def main() -> int:
    args = parse_args()
    for name in ("output_dir", "ledger", "selection", "webcraft_root", "index_dir"):
        setattr(args, name, getattr(args, name).resolve())
    if args.offset < 0 or args.limit < 0:
        raise ValueError("offset and limit must be nonnegative")
    if not 0 <= args.request_interval <= 60:
        raise ValueError("request interval must be between 0 and 60 seconds")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise RuntimeError("output directory must be empty")
    if not PREREG.is_file() or not args.selection.is_file():
        raise RuntimeError("preregistration and frozen selection must exist before execution")
    for method, expected in PROMPT_HASHES.items():
        actual = hashlib.sha256(public_system_prompt(method).encode()).hexdigest()
        if actual != expected:
            raise RuntimeError(f"{method} prompt changed after preregistration")

    planned = args.planned_memory_mib * 1024**2
    required = memory_requirement(planned, int(1.8 * 1024**3))
    while effective_available_bytes() < required:
        print(json.dumps({"status": "waiting_for_memory", "poll_seconds": 600,
                          "required_bytes": required}), flush=True)
        time.sleep(600)
    available_before = require_memory(planned, reserve_bytes=int(1.8 * 1024**3))
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    pin, current_webcraft_commit = verify_frozen_files(args.webcraft_root)
    selection = json.loads(args.selection.read_text())
    if (selection["requested_model"] != MODEL
            or selection["required_server_model_identifier"] != MODEL):
        raise RuntimeError("locked model identifier changed")
    full_jobs = [row for row in selection["jobs"] if row["method"] in args.methods]
    counts = Counter(row["method"] for row in full_jobs)
    if counts != Counter({method: EXPECTED[method] for method in args.methods}):
        raise RuntimeError(f"frozen job count mismatch: {counts}")
    if (args.offset or args.limit) and len(args.methods) != 1:
        raise ValueError("sharded execution requires exactly one method")
    jobs = full_jobs[args.offset:]
    if args.limit:
        jobs = jobs[:args.limit]
    if not jobs:
        raise ValueError("selected shard is empty")
    forbidden = {"feasible", "oracle_cost", "oracle_plans", "conflict_ids", "conflicts_by_world"}
    if any(forbidden & set(row) or forbidden & set(row["public_task"]) for row in jobs):
        raise RuntimeError("selection leaked evaluator-only task fields")

    args.output_dir.mkdir(parents=True)
    client = OpenAIProbeClient(args.ledger, model=MODEL)
    client.min_interval_seconds = args.request_interval
    manifest = {
        "schema_version": 1,
        "record_type": "alias_locked_replication_shard",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
        "required_server_model_identifier": MODEL,
        "methods": args.methods,
        "episodes": len(jobs),
        "full_method_episodes": len(full_jobs),
        "job_offset": args.offset,
        "job_limit": args.limit,
        "max_model_calls": 40,
        "max_actions": 90,
        "temperature": 0.0,
        "max_tokens": 4096,
        "thinking": "disabled",
        "trace_retention_steps": 8,
        "reading_interface": "raw_public_search_snippets",
        "infrastructure_retries_per_episode": 2,
        "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "external_prompt_code_sha256": hashlib.sha256(
            (ROOT / "src/arbiter/evaluation/external_agents.py").read_bytes()
        ).hexdigest(),
        "prompt_sha256": {method: PROMPT_HASHES[method] for method in args.methods},
        "webcraft_pinned_commit": pin["webcraft_commit"],
        "webcraft_current_commit": current_webcraft_commit,
        "webcraft_commit_drift": current_webcraft_commit != pin["webcraft_commit"],
        "code_commit": subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT, text=True).strip(),
        "required_memory_bytes": required,
        "available_before_bytes": available_before,
        "jobs": [{k: row[k] for k in ("method", "condition", "world", "task_id", "task_type")}
                 for row in jobs],
    }
    (args.output_dir / "run_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    sim = load_frozen_sim(args.webcraft_root)
    method_status: dict[str, Any] = {}
    episode_count = 0
    started = time.monotonic()
    for method in args.methods:
        method_jobs = [row for row in jobs if row["method"] == method]
        method_models: set[str] = set()
        client.returned_models.clear()
        stop_reason = None
        try:
            client.validate_canary(thinking="disabled")
            method_models.update(client.returned_models)
        except GatewayError as exc:
            stop_reason = f"preflight_{type(exc).__name__}"
        for job in method_jobs:
            if stop_reason:
                break
            final_row = None
            for attempt in range(1, 4):
                action_path = (args.output_dir / "actions" /
                               f'{method}__{job["condition"]}__{job["world"]}__{job["task_id"]}__attempt{attempt}.jsonl')
                public = job["public_task"]
                initial = Counter({item["name"]: int(item["count"])
                                   for item in public["standard_items"] + public.get("task_supplements", [])})
                handle = sim.create_simulated_agent(
                    version=job["world"], log_path=action_path, initial_inventory=initial,
                    search_condition=job["condition"],
                    search_index_dir=str(args.index_dir / job["condition"]),
                )
                api = PublicSemanticAdapter(handle["api"])
                before = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
                try:
                    episode = run_external_episode(
                        api, client, method=method, task=public,
                        max_calls=40, max_actions=90, thinking="disabled",
                    )
                except GatewayError as exc:
                    retryable = retryable_gateway_failure(exc, args.ledger)
                    append_jsonl(args.output_dir / "retry_audit.jsonl", {
                        "method": method, "condition": job["condition"], "world": job["world"],
                        "task_id": job["task_id"], "attempt": attempt,
                        "error_type": type(exc).__name__, "retry": retryable and attempt < 3,
                    })
                    handle["shutdown"]()
                    if not retryable:
                        stop_reason = type(exc).__name__
                        break
                    if attempt == 3:
                        stop_reason = "infrastructure_retries_exhausted"
                    continue
                if episode.model_output_error:
                    append_jsonl(args.output_dir / "aborted.jsonl", {
                        "method": method, "condition": job["condition"], "world": job["world"],
                        "task_id": job["task_id"], "attempt": attempt,
                        "error_type": "model_output_error", "error_code": episode.model_output_error,
                        "included_in_metrics": False,
                    })
                    handle["shutdown"]()
                    stop_reason = "model_output_error"
                    break
                if not episode.submitted:
                    api.submit({"feasible": None}, intent="commit")
                final_inventory = api.inventory(intent="commit")
                handle["evaluator"].finish_episode()
                handle["shutdown"]()
                after = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
                method_models.update(client.returned_models)
                if method_models != {MODEL}:
                    stop_reason = "server_model_identifier_not_locked"
                final_row = {
                    "schema_version": 1,
                    "record_type": "alias_locked_episode",
                    "method": method,
                    "condition": job["condition"],
                    "world": job["world"],
                    "task_id": job["task_id"],
                    "task_type": job["task_type"],
                    "seed": job["seed"],
                    "model_requested": MODEL,
                    "server_model_identifiers": sorted(method_models),
                    "reported_feasible": episode.reported_feasible,
                    "terminal_runtime": "completed",
                    "model_calls": after[0] - before[0],
                    "input_tokens": after[1] - before[1],
                    "output_tokens": after[2] - before[2],
                    "nominal_cost_usd": None,
                    "actual_provider_cost": None,
                    "diagnostics": list(episode.diagnostics),
                    "final_inventory": final_inventory,
                    "action_log": str(action_path.relative_to(ROOT)),
                    "attempts": attempt,
                }
                break
            if final_row is not None:
                append_jsonl(args.output_dir / "episodes.jsonl", final_row)
                episode_count += 1
                print(json.dumps({"method": method, "completed": episode_count,
                                  "task_id": job["task_id"], "calls": final_row["model_calls"]}), flush=True)
            if stop_reason:
                break
        completed = sum(
            1 for line in (args.output_dir / "episodes.jsonl").read_text().splitlines()
            if json.loads(line)["method"] == method
        ) if (args.output_dir / "episodes.jsonl").is_file() else 0
        method_status[method] = {
            "status": "stopped" if stop_reason else "completed",
            "stop_reason": stop_reason,
            "server_model_identifiers": sorted(method_models),
            "episodes_completed": completed,
        }

    status = {
        "status": "completed" if all(row["status"] == "completed" for row in method_status.values()) else "partial",
        "methods": method_status,
        "episodes_completed": episode_count,
        "successful_provider_responses": client.calls,
        "task_calls": client.task_calls,
        "input_tokens": client.input_tokens,
        "output_tokens": client.output_tokens,
        "actual_provider_cost": client.priced_cost,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_before_kib": rss_before,
        "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "available_after_bytes": effective_available_bytes(),
    }
    (args.output_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, indent=2, sort_keys=True))
    return 0 if status["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
