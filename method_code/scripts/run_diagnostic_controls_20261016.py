"""Run a fixed A7 diagnostic-control shard with privileged facts as public evidence."""
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
from arbiter.evaluation.diagnostic_controls import (
    DIAGNOSTIC_INSTANCES, DiagnosticFactsAdapter, evidence_rows, s_initial_inventory,
)
from arbiter.evaluation.external_agents import public_system_prompt, run_external_episode
from arbiter.evaluation.external_controls import public_task
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace
from arbiter.llm.gateway import GatewayError
from run_external_controls import RetryingClient


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
PREREG = ROOT / "prereg/diagnostic_controls_20261016.md"
MODEL = "glm-5.2"
PROMPT_HASHES = {
    "direct_llm": "eb39e819b15453aa8b2d8a811b627a1a0d6bc3e3b93307b87637bba59f94a352",
    "react": "5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84",
}


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--method", choices=tuple(PROMPT_HASHES), required=True)
    parser.add_argument("--condition", choices=("K", "K+C", "K+N", "S"), required=True)
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--task-ids-file", type=Path,
                        help="Exact preregistered recovery task IDs; preserves completed episodes")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--request-interval", type=float, default=2.0)
    return parser.parse_args()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_pin(root: Path) -> tuple[str, str]:
    pin = json.loads((ROOT / "config/webcraft_pin.json").read_text())
    bad = []
    for group in pin["roots"].values():
        for relative, expected in group.get("files", {}).items():
            path = root / relative
            if not path.is_file() or digest(path) != expected:
                bad.append(relative)
    if bad:
        raise RuntimeError(f"frozen WebCraft file mismatch: {bad}")
    current = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
    return str(pin["webcraft_commit"]), current


def load_sim(root: Path) -> Any:
    if str(root.resolve()) not in sys.path:
        sys.path.insert(0, str(root.resolve()))
    return importlib.import_module("sim")


def load_requirements(root: Path) -> Any:
    resolved = str(root.resolve())
    if resolved not in sys.path:
        sys.path.insert(0, resolved)
    return importlib.import_module("corpus.solvability_audit").requirements_from_task


def goal_count(inventory: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return sum(int(row["count"]) for row in inventory if str(row["name"]).removeprefix("minecraft:") == target["item"])


def write_jsonl(path: Path, value: dict[str, Any]) -> None:
    with path.open("a") as handle:
        handle.write(json.dumps(value, sort_keys=True) + "\n")


def main() -> int:
    parsed = args()
    for name in ("output_dir", "ledger", "webcraft_root"):
        setattr(parsed, name, getattr(parsed, name).resolve())
    if parsed.offset < 0 or parsed.limit < 0 or not 0 <= parsed.request_interval <= 60:
        raise ValueError("invalid shard bounds or request interval")
    if parsed.output_dir.exists() and any(parsed.output_dir.iterdir()):
        raise RuntimeError("output directory must be empty")
    if os.environ.get("WC_LLM_MODEL") != MODEL:
        raise RuntimeError("requested model identifier is not glm-5.2")
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")
    if hashlib.sha256(public_system_prompt(parsed.method).encode()).hexdigest() != PROMPT_HASHES[parsed.method]:
        raise RuntimeError("external prompt changed after preregistration")

    pin, current = verify_pin(parsed.webcraft_root)
    tasks = {row["id"]: row for row in rows(parsed.webcraft_root / "data/tasks.jsonl") if row.get("split") == "eval"}
    jobs = [{"task_id": item, "world": world, "source_condition": source} for item, world, source in DIAGNOSTIC_INSTANCES]
    if parsed.method == "direct_llm" and parsed.condition != "K":
        raise ValueError("Direct LLM is preregistered only for K")
    if parsed.condition == "S" and parsed.method != "react":
        raise ValueError("S is preregistered only for ReAct")
    if parsed.task_ids_file:
        selected = json.loads(parsed.task_ids_file.read_text())
        fields = {"task_id", "world", "source_condition"}
        if (not isinstance(selected, list) or not selected
                or any(not isinstance(item, dict) or set(item) != fields for item in selected)):
            raise ValueError("task-ids-file must be a nonempty JSON list of exact task/world/source-condition records")
        selected_keys = {(str(item["task_id"]), str(item["world"]), str(item["source_condition"])) for item in selected}
        available_keys = {(row["task_id"], row["world"], row["source_condition"]) for row in jobs}
        unknown = selected_keys - available_keys
        if unknown:
            raise ValueError(f"task-ids-file includes non-preregistered instances: {sorted(unknown)}")
        jobs = [row for row in jobs if (row["task_id"], row["world"], row["source_condition"]) in selected_keys]
    jobs = jobs[parsed.offset:]
    if parsed.limit:
        jobs = jobs[:parsed.limit]
    if not jobs:
        raise ValueError("empty A7 shard")
    parsed.output_dir.mkdir(parents=True)
    (parsed.output_dir / "run_manifest.json").write_text(json.dumps({
        "schema_version": 1, "record_type": "a7_diagnostic_shard", "started_at": datetime.now(timezone.utc).isoformat(),
        "method": parsed.method, "condition": parsed.condition, "episodes": len(jobs), "jobs": jobs,
        "requested_model": MODEL, "required_server_model_identifier": MODEL, "max_model_calls": 40, "max_actions": 90,
        "temperature": 0.0, "max_tokens": 4096, "thinking": "disabled", "prompt_sha256": PROMPT_HASHES[parsed.method],
        "prereg_sha256": digest(PREREG), "webcraft_pinned_commit": pin, "webcraft_current_commit": current,
    }, indent=2, sort_keys=True) + "\n")

    sim = load_sim(parsed.webcraft_root)
    requirements_from_task = load_requirements(parsed.webcraft_root)
    conflicts = load_conflicts(parsed.webcraft_root / "data/conflicts.jsonl")
    nodes = load_resource_nodes(parsed.webcraft_root / "harness/config/arena.json")
    # Retries are limited to two identical retransmissions for transient provider
    # responses.  They do not change the selected task, prompt, or credential.
    client = RetryingClient(parsed.ledger, model=MODEL)
    client.min_interval_seconds = parsed.request_interval
    started, completed, stop_reason = time.monotonic(), 0, None
    model_ids: set[str] = set()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        client.validate_canary(thinking="disabled")
        model_ids.update(client.returned_models)
    except GatewayError as exc:
        stop_reason = f"preflight_{type(exc).__name__}"

    for job in jobs:
        if stop_reason:
            break
        task = tasks[job["task_id"]]
        initial = Counter({row["name"]: int(row["count"]) for row in task["standard_items"] + task.get("task_supplements", [])})
        s_metadata = None
        if parsed.condition == "S":
            initial, s_metadata = s_initial_inventory(task, job["world"], conflicts)
        action_path = parsed.output_dir / "actions" / f"{parsed.method}__{parsed.condition}__{job['world']}__{job['task_id']}.jsonl"
        handle = sim.create_simulated_agent(version=job["world"], log_path=action_path, initial_inventory=initial,
                                            search_condition=job["source_condition"], search_index_dir=str(parsed.webcraft_root / "corpus/indexes/formal_p1" / job["source_condition"]))
        evidence_condition = "K+C" if parsed.condition == "S" else parsed.condition
        evidence = evidence_rows(task, job["world"], evidence_condition, webcraft_root=parsed.webcraft_root,
                                 requirements_from_task=requirements_from_task, conflicts=conflicts)
        api: Any = DiagnosticFactsAdapter(PublicSemanticAdapter(handle["api"]), evidence)
        before = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
        episode = None
        try:
            episode = run_external_episode(api, client, method=parsed.method, task=public_task(task), max_calls=40, max_actions=90, thinking="disabled")
            if episode.model_output_error:
                api.submit({"feasible": None}, intent="commit")
            final_inventory = api.inventory(intent="commit")
            handle["evaluator"].finish_episode()
        except GatewayError as exc:
            stop_reason = type(exc).__name__
        finally:
            handle["shutdown"]()
        if stop_reason:
            break
        assert episode is not None
        observed_models = {str(d["model"]) for d in episode.diagnostics if d.get("model")}
        model_ids.update(observed_models)
        if model_ids != {MODEL}:
            stop_reason = "server_model_identifier_not_locked"
            break
        events = rows(action_path)
        feasible = bool(task["feasible"][job["world"]])
        attained = goal_count(final_inventory, task["target"]) >= int(task["target"]["count"])
        success = int((feasible and attained and episode.reported_feasible is True) or (not feasible and episode.reported_feasible is False))
        legacy = score_external_trace(events, world=job["world"], conflicts=conflicts, resource_nodes=nodes,
                                      true_feasible=feasible, reported_feasible=episode.reported_feasible)
        scored = outcome({"world": job["world"], "true_feasible": feasible, "reported_feasible": episode.reported_feasible,
                          "metrics": {"SR": success}, "model_calls": client.task_calls - before[0], "diagnostics": list(episode.diagnostics),
                          "_max_calls": 40, "_max_actions": 90}, events, task)
        (parsed.output_dir / "evidence").mkdir(exist_ok=True)
        evidence_path = parsed.output_dir / "evidence" / f"{parsed.condition}__{job['world']}__{job['task_id']}.json"
        evidence_path.write_text(json.dumps({"schema_version": 1, "public_search_rows": evidence, "search_observations": api.observations}, indent=2, sort_keys=True) + "\n")
        after = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
        write_jsonl(parsed.output_dir / "episodes.jsonl", {"schema_version": 1, "record_type": "a7_diagnostic_episode", **job,
            "method": parsed.method, "condition": parsed.condition, "seed": 20261016, "model_requested": MODEL,
            "server_model_identifiers": sorted(observed_models), "true_feasible": feasible, "reported_feasible": episode.reported_feasible,
            "success": success, "model_calls": after[0] - before[0], "input_tokens": after[1] - before[1], "output_tokens": after[2] - before[2],
            "tool_actions": episode.tool_actions, "primary_cost": episode.primary_cost, "model_output_error": episode.model_output_error,
            "metrics": legacy, "gold_score": scored, "action_log": str(action_path.relative_to(ROOT)), "evidence_path": str(evidence_path.relative_to(ROOT)),
            "s_state_metadata": s_metadata})
        completed += 1
        print(json.dumps({"completed": completed, "requested": len(jobs), "task_id": job["task_id"], "method": parsed.method, "condition": parsed.condition}), flush=True)
    status = {"status": "completed" if not stop_reason else "partial", "stop_reason": stop_reason, "method": parsed.method,
              "condition": parsed.condition, "episodes_requested": len(jobs), "episodes_completed": completed,
              "server_model_identifiers": sorted(model_ids), "successful_provider_responses": client.calls, "task_calls": client.task_calls,
              "input_tokens": client.input_tokens, "output_tokens": client.output_tokens, "actual_provider_cost": client.priced_cost,
              "elapsed_seconds": time.monotonic() - started, "peak_rss_before_kib": rss_before,
              "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "available_after_bytes": effective_available_bytes()}
    (parsed.output_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, sort_keys=True), flush=True)
    return 0 if not stop_reason else 2


if __name__ == "__main__":
    raise SystemExit(main())
