"""Run one fixed A8 CDS external-agent shard."""
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
from arbiter.evaluation.cds_agents import (
    BOUNDARY_INSTRUCTION, CDSKnowledgeAdapter, boundary_metrics, boundary_truth,
    digest, parse_boundary, public_rows, replace_search_results, verify_w7a_freeze,
)
from arbiter.evaluation.external_agents import public_system_prompt, run_external_episode
from arbiter.evaluation.external_controls import public_task
from arbiter.evaluation.external_rescore import outcome
from arbiter.evaluation.external_scoring import load_conflicts, load_resource_nodes, score_external_trace
from arbiter.llm.gateway import GatewayError
from run_external_controls import RetryingClient


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
PREREG = ROOT / "prereg/cds_agents_20261017.md"
MODEL = "glm-5.2"
REGISTERED_WEBCRAFT_COMMIT = "a18d643f8fcc2b912e78a014b14346833fb0384f"
PROMPT_HASHES = {
    "direct_llm": "eb39e819b15453aa8b2d8a811b627a1a0d6bc3e3b93307b87637bba59f94a352",
    "react": "5fbce9d44f33b7057a8ca56512ab29071c6498919988f76e48786009fb003f84",
}
METHOD_CONDITIONS = {
    "direct_llm": ("K", "K+S", "empty"),
    "react": ("K", "K+S", "S", "empty", "K+N"),
}


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--jobs-file", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--ledger", type=Path, required=True)
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--request-interval", type=float, default=2.0)
    return parser.parse_args()


def rows(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def append(path: Path, row: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a") as handle:
        handle.write(json.dumps(row, sort_keys=True, ensure_ascii=False) + "\n")


def all_jobs(root: Path) -> list[dict[str, str]]:
    manifest = json.loads((root / "cds_materials/cds_manifest.json").read_text())
    jobs = []
    for method, conditions in METHOD_CONDITIONS.items():
        for condition in conditions:
            for row in manifest["rows"]:
                jobs.append({"method": method, "condition": condition,
                             "instance_id": row["instance_id"]})
    return jobs


def validate_jobs(path: Path, root: Path) -> list[dict[str, str]]:
    selected = json.loads(path.read_text())
    if not isinstance(selected, list) or not selected:
        raise ValueError("jobs file must be a nonempty JSON list")
    valid = {(row["method"], row["condition"], row["instance_id"]) for row in all_jobs(root)}
    keys = []
    for row in selected:
        if not isinstance(row, dict) or set(row) != {"method", "condition", "instance_id"}:
            raise ValueError("invalid A8 job fields")
        key = (row["method"], row["condition"], row["instance_id"])
        if key not in valid or key in keys:
            raise ValueError(f"invalid or duplicate A8 job: {key}")
        keys.append(key)
    return selected


def load_sim(root: Path) -> Any:
    value = str(root.resolve())
    if value not in sys.path:
        sys.path.insert(0, value)
    return importlib.import_module("sim")


def goal_count(inventory: list[dict[str, Any]], target: dict[str, Any]) -> int:
    return sum(int(row["count"]) for row in inventory
               if str(row["name"]).removeprefix("minecraft:") == target["item"])


class AuditedClient(RetryingClient):
    """Persist full messages without changing the gateway request."""

    audit_path: Path | None = None

    def chat(self, call_type: str, messages: list[dict[str, str]], **kwargs: Any) -> dict[str, Any]:
        request_record = {
            "record_type": "full_prompt", "call_type": call_type,
            "messages": messages, "parameters": {
                key: kwargs.get(key) for key in ("temperature", "max_tokens", "json_object", "thinking")
            },
        }
        if self.audit_path is not None:
            append(self.audit_path, request_record)
        try:
            response = super().chat(call_type, messages, **kwargs)
        except Exception as exc:
            if self.audit_path is not None:
                append(self.audit_path, {"record_type": "response_error", "call_type": call_type,
                                         "error_type": type(exc).__name__})
            raise
        if self.audit_path is not None:
            append(self.audit_path, {"record_type": "full_response", "call_type": call_type,
                                     "model": response.get("model"), "content": response.get("content"),
                                     "finish_reason": response.get("finish_reason"), "usage": response.get("usage")})
        if response.get("model") != MODEL:
            raise GatewayError("server_model_identifier_not_locked")
        return response


def boundary_call(client: AuditedClient, *, task: dict[str, Any], trace: list[dict[str, Any]],
                  candidates: list[dict[str, str]]) -> tuple[dict[str, Any], list[bool | None]]:
    payload = {"task": public_task(task), "episode_trace": trace,
               "candidates": [{"index": index, **row} for index, row in enumerate(candidates, 1)]}
    response = client.chat(
        "cds_boundary", [{"role": "system", "content": BOUNDARY_INSTRUCTION},
                         {"role": "user", "content": json.dumps(payload, sort_keys=True)}],
        temperature=0.0, max_tokens=4096, json_object=True, thinking="disabled",
    )
    return response, parse_boundary(response.get("content", ""), len(candidates))


def main() -> int:
    parsed = args()
    for name in ("jobs_file", "output_dir", "ledger", "webcraft_root"):
        setattr(parsed, name, getattr(parsed, name).resolve())
    if parsed.output_dir.exists() and any(parsed.output_dir.iterdir()):
        raise RuntimeError("output directory must be empty")
    if not 0 <= parsed.request_interval <= 60:
        raise ValueError("request interval must be between 0 and 60 seconds")
    if os.environ.get("WC_LLM_MODEL") != MODEL:
        raise RuntimeError("A8 requires requested model glm-5.2")
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")
    if not PREREG.is_file():
        raise RuntimeError("A8 preregistration missing")
    freeze = verify_w7a_freeze(parsed.webcraft_root)
    current_commit = subprocess.check_output(
        ["git", "-C", str(parsed.webcraft_root), "rev-parse", "HEAD"], text=True,
    ).strip()
    ancestry = subprocess.run(
        ["git", "-C", str(parsed.webcraft_root), "merge-base", "--is-ancestor",
         REGISTERED_WEBCRAFT_COMMIT, current_commit],
        check=False,
    )
    if ancestry.returncode != 0:
        raise RuntimeError("WebCraft HEAD does not descend from the A8 registered commit")
    jobs = validate_jobs(parsed.jobs_file, parsed.webcraft_root)
    for method in {row["method"] for row in jobs}:
        if hashlib.sha256(public_system_prompt(method).encode()).hexdigest() != PROMPT_HASHES[method]:
            raise RuntimeError("external prompt changed after preregistration")

    parsed.output_dir.mkdir(parents=True)
    (parsed.output_dir / "run_manifest.json").write_text(json.dumps({
        "schema_version": 1, "record_type": "a8_cds_agent_shard",
        "started_at": datetime.now(timezone.utc).isoformat(), "jobs": jobs,
        "requested_model": MODEL, "required_server_model_identifier": MODEL,
        "max_model_calls": 40, "max_actions": 90, "boundary_calls_per_episode": 1,
        "temperature": 0.0, "max_tokens": 4096, "thinking": "disabled",
        "prompt_sha256": PROMPT_HASHES, "boundary_prompt_sha256": hashlib.sha256(BOUNDARY_INSTRUCTION.encode()).hexdigest(),
        "prereg_sha256": digest(PREREG),
        "webcraft_registered_commit": REGISTERED_WEBCRAFT_COMMIT,
        "webcraft_runtime_commit": current_commit,
        "w7a_freeze_inputs": len(freeze["inputs"]), "w7a_freeze_sha256": digest(parsed.webcraft_root / "config/ext_v2/freeze_manifest.json"),
    }, indent=2, sort_keys=True) + "\n")

    manifest = json.loads((parsed.webcraft_root / "cds_materials/cds_manifest.json").read_text())
    manifest_rows = {row["instance_id"]: row for row in manifest["rows"]}
    tasks = {row["id"]: row for row in rows(parsed.webcraft_root / "data/ext_v2/ext_v2_tasks_20261016.jsonl")}
    sim = load_sim(parsed.webcraft_root)
    conflicts = load_conflicts(parsed.webcraft_root / "data/conflicts.jsonl")
    nodes = load_resource_nodes(parsed.webcraft_root / "harness/config/arena.json")
    client = AuditedClient(parsed.ledger, model=MODEL)
    client.min_interval_seconds = parsed.request_interval
    started, completed, stop_reason = time.monotonic(), 0, None
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    try:
        client.validate_canary(thinking="disabled")
    except GatewayError as exc:
        stop_reason = f"preflight_{type(exc).__name__}"

    for job in jobs:
        if stop_reason:
            break
        meta = manifest_rows[job["instance_id"]]
        material = json.loads((parsed.webcraft_root / meta["visible_path"]).read_text())
        evaluator = json.loads((parsed.webcraft_root / meta["evaluator_path"]).read_text())
        if material["instance_id"] != evaluator["instance_id"] or material["instance_id"] != job["instance_id"]:
            raise RuntimeError("CDS visible/evaluator instance mismatch")
        task = tasks[material["task_id"]]
        initial = Counter({row["name"]: int(row["count"])
                           for row in task["standard_items"] + task.get("task_supplements", [])})
        stem = f"{job['method']}__{job['condition']}__{job['instance_id']}"
        action_path = parsed.output_dir / "actions" / f"{stem}.jsonl"
        prompt_path = parsed.output_dir / "prompts" / f"{stem}.jsonl"
        prompt_path.parent.mkdir(parents=True, exist_ok=True)
        client.audit_path = prompt_path
        source_condition = f"matched_{material['target_world']}"
        handle = sim.create_simulated_agent(
            version=material["target_world"], log_path=action_path,
            initial_inventory=initial, search_condition=source_condition,
            search_index_dir=str(parsed.webcraft_root / "corpus/indexes/formal_p1" / source_condition),
        )
        api = CDSKnowledgeAdapter(PublicSemanticAdapter(handle["api"]), public_rows(material, job["condition"]))
        before = (client.task_calls, client.task_input_tokens, client.task_output_tokens,
                  client.task_priced_cost)
        episode = None
        boundary_response = None
        try:
            episode = run_external_episode(
                api, client, method=job["method"], task=public_task(task),
                max_calls=40, max_actions=90, thinking="disabled",
            )
            action_after = (client.task_calls, client.task_input_tokens, client.task_output_tokens)
            if episode.model_output_error:
                api.submit({"feasible": None}, intent="commit")
            final_inventory = api.inventory(intent="commit")
            handle["evaluator"].finish_episode()
            boundary_response, predictions = boundary_call(
                client, task=task, trace=api.trace, candidates=material["boundary_question"],
            )
        except GatewayError as exc:
            stop_reason = type(exc).__name__
            append(parsed.output_dir / "aborted.jsonl", {**job, "error_type": stop_reason,
                                                          "included_in_metrics": False})
        finally:
            handle["shutdown"]()
        if stop_reason:
            break
        assert episode is not None and boundary_response is not None
        replace_search_results(action_path, api.observations)
        events = rows(action_path)
        feasible = bool(task["feasible"][material["target_world"]])
        attained = goal_count(final_inventory, task["target"]) >= int(task["target"]["count"])
        success = int((feasible and attained and episode.reported_feasible is True)
                      or (not feasible and episode.reported_feasible is False))
        legacy = score_external_trace(
            events, world=material["target_world"], conflicts=conflicts,
            resource_nodes=nodes, true_feasible=feasible,
            reported_feasible=episode.reported_feasible,
        )
        scored = outcome({
            "world": material["target_world"], "true_feasible": feasible,
            "reported_feasible": episode.reported_feasible,
            "metrics": {"SR": success}, "model_calls": action_after[0] - before[0],
            "diagnostics": list(episode.diagnostics), "_max_calls": 40, "_max_actions": 90,
        }, events, task)
        truth = boundary_truth(material, evaluator)
        bmetrics = boundary_metrics(predictions, truth, material["boundary_question"])
        after = (client.task_calls, client.task_input_tokens, client.task_output_tokens,
                 client.task_priced_cost)
        observed_models = sorted({str(row.get("model")) for row in episode.diagnostics if row.get("model")}
                                 | {str(boundary_response.get("model"))})
        if observed_models != [MODEL]:
            stop_reason = "server_model_identifier_not_locked"
            break
        append(parsed.output_dir / "episodes.jsonl", {
            "schema_version": 1, "record_type": "a8_cds_agent_episode", **job,
            "task_id": task["id"], "family_id": task["independent_family_id"],
            "target_world": material["target_world"], "comparison_world": material["comparison_world"],
            "seed": 20261017, "model_requested": MODEL, "server_model_identifiers": observed_models,
            "true_feasible": feasible, "reported_feasible": episode.reported_feasible,
            "success": success, "terminal_attained": attained,
            "action_model_calls": action_after[0] - before[0], "boundary_model_calls": after[0] - action_after[0],
            "model_calls": after[0] - before[0], "input_tokens": after[1] - before[1],
            "output_tokens": after[2] - before[2], "tool_actions": episode.tool_actions,
            "primary_cost": episode.primary_cost, "model_output_error": episode.model_output_error,
            "metrics": legacy, "gold_score": scored, "boundary_predictions": predictions,
            "boundary_truth": truth, "boundary": bmetrics,
            "action_log": str(action_path.relative_to(ROOT)),
            "prompt_log": str(prompt_path.relative_to(ROOT)),
            "visible_material_path": meta["visible_path"], "visible_material_sha256": meta["visible_sha256"],
            "search_observations": api.observations,
            "estimated_provider_cost": None if after[3] is None or before[3] is None else after[3] - before[3],
        })
        completed += 1
        print(json.dumps({"completed": completed, "requested": len(jobs), **job}), flush=True)

    client.audit_path = None
    status = {
        "status": "completed" if not stop_reason else "partial", "stop_reason": stop_reason,
        "episodes_requested": len(jobs), "episodes_completed": completed,
        "server_model_identifiers": sorted(client.returned_models),
        "successful_provider_responses": client.calls, "task_calls": client.task_calls,
        "input_tokens": client.input_tokens, "output_tokens": client.output_tokens,
        "actual_provider_cost": client.priced_cost,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_before_kib": rss_before,
        "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "available_after_bytes": effective_available_bytes(),
    }
    (parsed.output_dir / "status.json").write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
    print(json.dumps(status, sort_keys=True), flush=True)
    return 0 if not stop_reason else 2


if __name__ == "__main__":
    raise SystemExit(main())
