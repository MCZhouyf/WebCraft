"""Run the preregistered second-model probe without evaluator-side inputs."""
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
from arbiter.evaluation.external_agents import run_external_episode
from arbiter.llm.gateway import (
    BudgetExceeded, CanaryFailed, GatewayError, GatewayProtocolError,
)
from arbiter.llm.openai_probe import OpenAIProbeClient, ProbeRefusal


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
MODEL = "glm-5.2"
SELECTION = ROOT / "prereg/external_second_model_sample_20261011.json"
PREREG = ROOT / "prereg/external_second_model_probe_20261011.md"
AMENDMENT = ROOT / "prereg/external_second_model_probe_amendment_20261011.md"


def verify_frozen_files(root: Path) -> tuple[dict, str]:
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
    parser.add_argument("--methods", choices=("direct_llm", "xenon"), nargs="+", required=True)
    parser.add_argument("--selection", type=Path, default=SELECTION)
    parser.add_argument("--output-dir", type=Path,
                        default=ROOT / "artifacts/post_review/external_second_model_probe")
    parser.add_argument("--ledger", type=Path,
                        default=ROOT / "logs/post_review/external_second_model_probe_ledger.jsonl")
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--index-dir", type=Path,
                        default=WEBCRAFT / "corpus/indexes/formal_p1")
    parser.add_argument("--request-interval", type=float, default=0.0)
    parser.add_argument("--planned-memory-mib", type=int, default=512)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def read_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    return [json.loads(line) for line in path.read_text().splitlines() if line]


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
    args.output_dir = args.output_dir.resolve()
    args.ledger = args.ledger.resolve()
    args.selection = args.selection.resolve()
    args.webcraft_root = args.webcraft_root.resolve()
    args.index_dir = args.index_dir.resolve()
    if args.offset < 0 or args.limit < 0:
        raise ValueError("offset and limit must be nonnegative")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise RuntimeError("output directory must be empty; existing probe results are immutable")
    if not PREREG.is_file() or not AMENDMENT.is_file() or not args.selection.is_file():
        raise RuntimeError("preregistration and frozen selection must exist before execution")
    planned = args.planned_memory_mib * 1024**2
    required = memory_requirement(planned, int(1.8 * 1024**3))
    while effective_available_bytes() < required:
        print(json.dumps({"status": "waiting_for_memory", "poll_seconds": 600,
                          "required_bytes": required}), flush=True)
        time.sleep(600)
    available_before = require_memory(planned, reserve_bytes=int(1.8 * 1024**3))
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    root = args.webcraft_root
    pin, current_webcraft_commit = verify_frozen_files(root)
    selection = json.loads(args.selection.read_text())
    if selection["model"] != MODEL or selection["webcraft_pinned_commit"] != pin["webcraft_commit"]:
        raise RuntimeError("selection model or WebCraft pin changed after preregistration")
    full_jobs = [row for row in selection["jobs"] if row["method"] in args.methods]
    expected = {"direct_llm": 110, "xenon": 40}
    counts = Counter(row["method"] for row in full_jobs)
    if counts != Counter({method: expected[method] for method in args.methods}):
        raise RuntimeError(f"frozen job count mismatch: {counts}")
    if (args.offset or args.limit) and len(args.methods) != 1:
        raise ValueError("sharded execution requires exactly one method")
    jobs = full_jobs[args.offset:]
    if args.limit:
        jobs = jobs[:args.limit]
    if not jobs:
        raise ValueError("selected shard is empty")
    # Runtime consumes only the frozen public job payload. It never opens task,
    # conflict, truth, claims-cache, or candidate-version files.
    forbidden_keys = {"feasible", "oracle_cost", "oracle_plans", "conflict_ids", "conflicts_by_world"}
    if any(forbidden_keys & set(row) or forbidden_keys & set(row["public_task"]) for row in jobs):
        raise RuntimeError("selection leaked evaluator-only task fields")

    args.output_dir.mkdir(parents=True)
    client = OpenAIProbeClient(args.ledger, model=MODEL)
    client.min_interval_seconds = args.request_interval
    manifest = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "model": MODEL,
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
        "infrastructure_retries_per_episode": 2,
        "selection_sha256": hashlib.sha256(args.selection.read_bytes()).hexdigest(),
        "prereg_sha256": hashlib.sha256(PREREG.read_bytes()).hexdigest(),
        "amendment_sha256": hashlib.sha256(AMENDMENT.read_bytes()).hexdigest(),
        "external_prompt_code_sha256": hashlib.sha256(
            (ROOT / "src/arbiter/evaluation/external_agents.py").read_bytes()
        ).hexdigest(),
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
    sim = load_frozen_sim(root)
    method_status = {}
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
        if len(method_models) >= 2:
            stop_reason = "multiple_server_model_identifiers"
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
                before = (
                    client.task_calls, client.task_input_tokens,
                    client.task_output_tokens,
                )
                refusal = False
                try:
                    episode = run_external_episode(
                        api, client, method=method, task=public,
                        max_calls=40, max_actions=90, thinking="disabled",
                    )
                except ProbeRefusal:
                    refusal = True
                    episode = None
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

                if refusal or episode is None or not episode.submitted:
                    api.submit({"feasible": None}, intent="commit")
                final_inventory = api.inventory(intent="commit")
                handle["evaluator"].finish_episode()
                handle["shutdown"]()
                after = (
                    client.task_calls, client.task_input_tokens,
                    client.task_output_tokens,
                )
                diagnostics = [] if episode is None else list(episode.diagnostics)
                diagnostics.extend({
                    "call": None, "model": model, "finish_reason": "refusal",
                    "input_tokens": after[1] - before[1], "output_tokens": after[2] - before[2],
                } for model in sorted(client.returned_models - method_models) if refusal)
                method_models.update(client.returned_models)
                if len(method_models) >= 2:
                    stop_reason = "multiple_server_model_identifiers"
                final_row = {
                    "schema_version": 1,
                    "record_type": "external_second_model_episode",
                    "method": method,
                    "condition": job["condition"],
                    "world": job["world"],
                    "task_id": job["task_id"],
                    "task_type": job["task_type"],
                    "seed": 20261006,
                    "model_requested": MODEL,
                    "server_model_identifiers": sorted(method_models),
                    "reported_feasible": None if episode is None else episode.reported_feasible,
                    "terminal_runtime": "model_refusal" if refusal else (
                        "model_output_error" if episode.model_output_error else "completed"
                    ),
                    "model_calls": after[0] - before[0],
                    "input_tokens": after[1] - before[1],
                    "output_tokens": after[2] - before[2],
                    "nominal_cost_usd": None,
                    "actual_provider_cost": None,
                    "diagnostics": diagnostics,
                    "final_inventory": final_inventory,
                    "action_log": str(action_path.relative_to(ROOT)),
                    "attempts": attempt,
                }
                break
            if final_row is not None:
                append_jsonl(args.output_dir / "episodes.jsonl", final_row)
                episode_count += 1
                print(json.dumps({
                    "method": method, "completed": episode_count,
                    "task_id": job["task_id"], "calls": final_row["model_calls"],
                    "terminal": final_row["terminal_runtime"],
                }), flush=True)
            if stop_reason:
                break
        method_status[method] = {
            "status": "stopped" if stop_reason else "completed",
            "stop_reason": stop_reason,
            "server_model_identifiers": sorted(method_models),
            "episodes_completed": sum(
                1 for line in (args.output_dir / "episodes.jsonl").read_text().splitlines()
                if json.loads(line)["method"] == method
            ) if (args.output_dir / "episodes.jsonl").is_file() else 0,
        }

    status = {
        "status": "completed" if all(row["status"] == "completed" for row in method_status.values()) else "partial",
        "methods": method_status,
        "episodes_completed": episode_count,
        "total_calls_including_canaries": client.calls,
        "task_calls": client.task_calls,
        "input_tokens": client.input_tokens,
        "output_tokens": client.output_tokens,
        "nominal_cost_usd": None,
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
