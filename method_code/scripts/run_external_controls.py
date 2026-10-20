"""Run one fixed shard of the preregistered external-agent controls."""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import resource
import subprocess
import sys
import time
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arbiter.adapters import PublicSemanticAdapter
from arbiter.checkpoint import effective_available_bytes
from arbiter.evaluation.external_agents import public_system_prompt, run_external_episode
from arbiter.evaluation.external_controls import (
    OracleSearchAdapter,
    apply_oracle_search_observations,
    matrix_jobs,
    oracle_search_rows,
    public_task,
)
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import (
    load_conflicts, load_resource_nodes, score_external_trace,
)
from arbiter.llm.gateway import (
    BudgetExceeded, CanaryFailed, GatewayError, GatewayProtocolError,
)
from arbiter.llm.openai_probe import OpenAIProbeClient, ProbeRefusal


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
MODEL = "glm-5.2"
PREREG = ROOT / "prereg/external_controls_20261013.md"
PROMPT_HASHES = {
    "direct_llm": "eb39e819b15453aa8b2d8a811b627a1a0d6bc3e3b93307b87637bba59f94a352",
    "react": "5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment", choices=("oracle_knowledge", "control_all_public", "conditions"), required=True)
    parser.add_argument("--method", choices=("direct_llm", "react"), required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--index-dir", type=Path, default=WEBCRAFT / "corpus/indexes/formal_p1")
    parser.add_argument("--request-interval", type=float, default=2.0)
    return parser.parse_args()


def jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append_jsonl(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True) + "\n")


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_frozen_webcraft(root: Path) -> tuple[str, str]:
    pin_path = ROOT / "config/webcraft_pin.json"
    pin = json.loads(pin_path.read_text())
    mismatches = []
    for group in pin["roots"].values():
        for relative, expected in group.get("files", {}).items():
            path = root / relative
            actual = sha256(path) if path.is_file() else None
            if actual != expected:
                mismatches.append(relative)
    if mismatches:
        raise RuntimeError(f"frozen WebCraft file mismatch: {mismatches}")
    current = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], text=True,
    ).strip()
    return str(pin["webcraft_commit"]), current


def load_sim(root: Path) -> Any:
    resolved = str(root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    return importlib.import_module("sim")


def retryable(exc: GatewayError, ledger: Path) -> bool:
    if isinstance(exc, (BudgetExceeded, CanaryFailed, GatewayProtocolError, ProbeRefusal)):
        return False
    if not ledger.is_file():
        return False
    records = jsonl(ledger)
    if not records:
        return False
    latest = records[-1]
    status = latest.get("http_status")
    return bool(
        status in {408, 429}
        or isinstance(status, int) and 500 <= status <= 599
        or latest.get("error_type") in {
            "URLError", "TimeoutError", "RemoteDisconnected", "IncompleteRead",
            "ConnectionResetError", "ConnectionAbortedError", "BrokenPipeError",
        }
    )


class RetryingClient(OpenAIProbeClient):
    """Retry the identical request in place, without resetting the episode."""

    def chat(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        for attempt in range(3):
            try:
                return super().chat(*args, **kwargs)
            except GatewayError as exc:
                if attempt == 2 or not retryable(exc, self.ledger):
                    raise
                append_jsonl(self.ledger.parent / "retry_audit.jsonl", {
                    "attempt": attempt + 1,
                    "error_type": type(exc).__name__,
                    "same_request": True,
                })
                time.sleep(10)
        raise AssertionError("unreachable")


def goal_count(inventory: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return sum(
        int(row["count"]) for row in inventory
        if str(row["name"]).removeprefix("minecraft:") == target["item"]
    )


def main() -> int:
    args = parse_args()
    for name in ("output_dir", "ledger", "webcraft_root", "index_dir"):
        setattr(args, name, getattr(args, name).resolve())
    if args.offset < 0 or args.limit < 0:
        raise ValueError("offset and limit must be nonnegative")
    if not 0 <= args.request_interval <= 60:
        raise ValueError("request interval must be between 0 and 60 seconds")
    if args.output_dir.exists() and any(args.output_dir.iterdir()):
        raise RuntimeError("output directory must be empty")
    if not PREREG.is_file():
        raise RuntimeError("external-controls preregistration is missing")
    if os.environ.get("WC_LLM_MODEL") != MODEL:
        raise RuntimeError("requested model identifier is not glm-5.2")
    actual_prompt = hashlib.sha256(public_system_prompt(args.method).encode()).hexdigest()
    if actual_prompt != PROMPT_HASHES[args.method]:
        raise RuntimeError("external prompt changed after preregistration")
    available_before = effective_available_bytes()
    if available_before < int(1.8 * 1024**3):
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")

    pinned_commit, current_commit = verify_frozen_webcraft(args.webcraft_root)
    tasks = jsonl(args.webcraft_root / "data/tasks.jsonl")
    eval_tasks = [row for row in tasks if row.get("split") == "eval"]
    task_map = {row["id"]: row for row in eval_tasks}
    full_jobs = matrix_jobs(eval_tasks, args.segment, (args.method,))
    jobs = full_jobs[args.offset:]
    if args.limit:
        jobs = jobs[:args.limit]
    if not jobs:
        raise ValueError("empty fixed shard")
    args.output_dir.mkdir(parents=True)
    manifest = {
        "schema_version": 1,
        "record_type": "external_controls_shard",
        "started_at": datetime.now(timezone.utc).isoformat(),
        "segment": args.segment,
        "method": args.method,
        "episodes": len(jobs),
        "full_method_episodes": len(full_jobs),
        "offset": args.offset,
        "limit": args.limit,
        "requested_model": MODEL,
        "required_server_model_identifier": MODEL,
        "max_model_calls": 40,
        "max_actions": 90,
        "temperature": 0.0,
        "max_tokens": 4096,
        "thinking": "disabled",
        "trace_retention_steps": 2 if args.method == "direct_llm" else 8,
        "prompt_sha256": actual_prompt,
        "prereg_sha256": sha256(PREREG),
        "webcraft_pinned_commit": pinned_commit,
        "webcraft_current_commit": current_commit,
        "request_interval_seconds": args.request_interval,
        "infrastructure_retries_per_request": 2,
        "jobs": jobs,
    }
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )

    sim = load_sim(args.webcraft_root)
    from corpus.solvability_audit import requirements_from_task
    client = RetryingClient(args.ledger, model=MODEL)
    client.min_interval_seconds = args.request_interval
    conflicts = load_conflicts(args.webcraft_root / "data/conflicts.jsonl")
    nodes = load_resource_nodes(args.webcraft_root / "harness/config/arena.json")
    started = time.monotonic()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    stop_reason = None
    completed = 0
    model_ids: set[str] = set()
    try:
        client.validate_canary(thinking="disabled")
        model_ids.update(client.returned_models)
    except GatewayError as exc:
        stop_reason = f"preflight_{type(exc).__name__}"

    for job in jobs:
        if stop_reason:
            break
        task = task_map[job["task_id"]]
        initial = Counter({
            row["name"]: int(row["count"])
            for row in task["standard_items"] + task.get("task_supplements", [])
        })
        action_path = args.output_dir / "actions" / (
            f'{args.method}__{args.segment}__{job["condition"]}__{job["world"]}__{job["task_id"]}.jsonl'
        )
        handle = sim.create_simulated_agent(
            version=job["world"],
            log_path=action_path,
            initial_inventory=initial,
            search_condition=job["condition"],
            search_index_dir=str(args.index_dir / job["condition"]),
        )
        base = PublicSemanticAdapter(handle["api"])
        oracle_rows: list[dict[str, Any]] = []
        if args.segment == "oracle_knowledge":
            oracle_rows = oracle_search_rows(
                task, job["world"], webcraft_root=args.webcraft_root,
                requirements_from_task=requirements_from_task,
            )
            api: Any = OracleSearchAdapter(base, oracle_rows)
        else:
            api = base
        before = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
        episode = None
        try:
            episode = run_external_episode(
                api, client, method=args.method, task=public_task(task),
                max_calls=40, max_actions=90, thinking="disabled",
            )
            if episode.model_output_error:
                api.submit({"feasible": None}, intent="commit")
            final_inventory = api.inventory(intent="commit")
            handle["evaluator"].finish_episode()
        except GatewayError as exc:
            stop_reason = type(exc).__name__
            append_jsonl(args.output_dir / "aborted.jsonl", {
                **job, "error_type": stop_reason, "included_in_metrics": False,
            })
        finally:
            handle["shutdown"]()
        if stop_reason:
            break
        assert episode is not None
        if args.segment == "oracle_knowledge":
            apply_oracle_search_observations(action_path, api.observations)
            evidence_path = args.output_dir / "oracle_evidence" / (
                f'{job["condition"]}__{job["world"]}__{job["task_id"]}.json'
            )
            evidence_path.parent.mkdir(parents=True, exist_ok=True)
            evidence_path.write_text(json.dumps({
                "schema_version": 1,
                "public_search_rows": oracle_rows,
                "search_observations": api.observations,
            }, indent=2, sort_keys=True) + "\n")
        after = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
        episode_models = {
            str(row["model"]) for row in episode.diagnostics if row.get("model")
        }
        model_ids.update(episode_models)
        if model_ids != {MODEL}:
            stop_reason = "server_model_identifier_not_locked"
            break
        events = jsonl(action_path)
        feasible = bool(task["feasible"][job["world"]])
        attained = goal_count(final_inventory, task["target"]) >= int(task["target"]["count"])
        success = int(
            feasible and attained and episode.reported_feasible is True
            or not feasible and episode.reported_feasible is False
        )
        legacy = score_external_trace(
            events, world=job["world"], conflicts=conflicts, resource_nodes=nodes,
            true_feasible=feasible, reported_feasible=episode.reported_feasible,
        )
        working = {
            "world": job["world"],
            "true_feasible": feasible,
            "reported_feasible": episode.reported_feasible,
            "metrics": {"SR": success},
            "model_calls": after[0] - before[0],
            "diagnostics": list(episode.diagnostics),
            "_max_calls": 40,
            "_max_actions": 90,
        }
        scored = outcome(working, events, task)
        append_jsonl(args.output_dir / "episodes.jsonl", {
            "schema_version": 1,
            "record_type": "external_control_episode",
            **job,
            "seed": 20261006,
            "setting": args.segment,
            "model_requested": MODEL,
            "server_model_identifiers": sorted(episode_models),
            "reported_feasible": episode.reported_feasible,
            "true_feasible": feasible,
            "success": success,
            "model_calls": after[0] - before[0],
            "input_tokens": after[1] - before[1],
            "output_tokens": after[2] - before[2],
            "tool_actions": episode.tool_actions,
            "primary_cost": episode.primary_cost,
            "model_output_error": episode.model_output_error,
            "metrics": legacy,
            "gold_score": scored,
            "action_log": str(action_path.relative_to(ROOT)),
            "oracle_public_row_count": len(oracle_rows),
        })
        completed += 1
        print(json.dumps({
            "method": args.method, "segment": args.segment,
            "completed": completed, "requested": len(jobs),
            "task_id": job["task_id"], "calls": after[0] - before[0],
        }), flush=True)

    status = {
        "status": "completed" if not stop_reason else "partial",
        "stop_reason": stop_reason,
        "segment": args.segment,
        "method": args.method,
        "episodes_requested": len(jobs),
        "episodes_completed": completed,
        "server_model_identifiers": sorted(model_ids),
        "successful_provider_responses": client.calls,
        "task_calls": client.task_calls,
        "input_tokens": client.input_tokens,
        "output_tokens": client.output_tokens,
        "actual_provider_cost": client.priced_cost,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_before_kib": rss_before,
        "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "available_before_bytes": available_before,
        "available_after_bytes": effective_available_bytes(),
    }
    (args.output_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, sort_keys=True), flush=True)
    return 0 if status["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
