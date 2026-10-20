"""Dynamically balance fixed external-control chunks over six private keys."""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from collections import deque
from pathlib import Path
from typing import Any

from arbiter.checkpoint import effective_available_bytes


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_CONFIG = Path("/root/.config/scope_credentials/glm52_pending.json")
COUNTS = {"oracle_knowledge": 110, "control_all_public": 60, "conditions": 132}
METHODS = ("direct_llm", "react")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment", choices=tuple(COUNTS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--chunk-size", type=int, default=8)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--credential-config", type=Path, default=PRIVATE_CONFIG)
    parser.add_argument(
        "--jobs-file", type=Path,
        help="Run an exact preregistered recovery job list instead of the full segment",
    )
    parser.add_argument(
        "--isolate-failed-slots", action="store_true",
        help="Quarantine a failed credential slot while healthy slots continue",
    )
    return parser.parse_args()


def chunks(segment: str, size: int) -> list[dict[str, Any]]:
    if size < 1:
        raise ValueError("chunk size must be positive")
    result = []
    for method in METHODS:
        for offset in range(0, COUNTS[segment], size):
            result.append({
                "method": method, "offset": offset,
                "limit": min(size, COUNTS[segment] - offset),
            })
    # Interleave methods while preserving each method's fixed offset order.
    return sorted(result, key=lambda row: (row["offset"], row["method"]))


def launch(job: dict[str, Any], slot: int, sequence: int, output: Path,
           config: dict[str, Any], interval: float) -> dict[str, Any]:
    method, offset, limit = job["method"], job["offset"], job["limit"]
    directory = output / f"chunk_{sequence:03d}_slot_{slot}_{method}_{offset}_{limit}"
    log_path = output / f"chunk_{sequence:03d}_slot_{slot}.log"
    env = os.environ.copy()
    env.update({
        "WC_LLM_API_KEY": config["api_keys"][slot - 1],
        "WC_LLM_BASE_URL": str(config["base_url"]),
        "WC_LLM_MODEL": "glm-5.2",
        "ARB_LLM_BUDGET": "1000000",
        "WC_LLM_PRICE_IN_PER_MTOK": "0",
        "WC_LLM_PRICE_OUT_PER_MTOK": "0",
        "CUDA_VISIBLE_DEVICES": "",
        "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "PYTHONPATH": str(ROOT / "src"),
    })
    env.pop("WC_LLM_API_KEYS", None)
    command = [
        "nice", "-n", "15", sys.executable,
        str(ROOT / "scripts/run_external_controls.py"),
        "--segment", job["segment"], "--method", method,
        "--offset", str(offset), "--limit", str(limit),
        "--output-dir", str(directory), "--ledger", str(directory / "ledger.jsonl"),
        "--request-interval", str(interval),
    ]
    handle = log_path.open("w")
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle,
                               stderr=subprocess.STDOUT, text=True)
    return {**job, "slot": slot, "sequence": sequence, "output_dir": directory,
            "log_path": log_path, "handle": handle, "process": process}


def main() -> int:
    args = parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("dynamic output directory must be empty")
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")
    config = json.loads(args.credential_config.read_text())
    if config.get("model") != "glm-5.2" or len(config.get("api_keys", [])) < 6:
        raise RuntimeError("six glm-5.2 credentials are required")
    output.mkdir(parents=True)
    if args.jobs_file:
        recovery = json.loads(args.jobs_file.read_text())
        if recovery.get("segment") != args.segment:
            raise RuntimeError("recovery jobs segment does not match --segment")
        planned = recovery.get("jobs")
        if not isinstance(planned, list) or not planned:
            raise RuntimeError("recovery jobs file must contain a nonempty jobs list")
        for row in planned:
            if set(row) != {"method", "offset", "limit"}:
                raise RuntimeError("recovery job fields must be method, offset, and limit")
            if row["method"] not in METHODS or row["offset"] < 0 or row["limit"] < 1:
                raise RuntimeError("invalid recovery job")
        planned = [{**row, "segment": args.segment} for row in planned]
    else:
        planned = [{**row, "segment": args.segment} for row in chunks(args.segment, args.chunk_size)]
    pending = deque(enumerate(planned, 1))
    active: dict[int, dict[str, Any]] = {}
    disabled_slots: set[int] = set()
    completed = []
    failures = []
    started = time.monotonic()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    manifest_rows = []
    for sequence, job in enumerate(planned, 1):
        manifest_rows.append({
            **job, "sequence": sequence,
            "output_dir": None,
        })
    (output / "parallel_manifest.json").write_text(json.dumps({
        "schema_version": 1, "scheduler": "dynamic_fixed_chunks",
        "segment": args.segment, "model": "glm-5.2", "workers": 6,
        "chunk_size": args.chunk_size, "shards": manifest_rows,
    }, indent=2, sort_keys=True) + "\n")

    stop = False
    while pending or active:
        for slot in range(1, 7):
            if stop or slot in disabled_slots or slot in active or not pending:
                continue
            sequence, job = pending.popleft()
            active[slot] = launch(job, slot, sequence, output, config, args.request_interval)
            time.sleep(1)
        time.sleep(1)
        for slot, row in list(active.items()):
            code = row["process"].poll()
            if code is None:
                continue
            row["handle"].close()
            del active[slot]
            relative = str(row["output_dir"].relative_to(ROOT))
            manifest_rows[row["sequence"] - 1]["output_dir"] = relative
            if code:
                failures.append({
                    "slot": slot, "sequence": row["sequence"], "exit_code": code,
                    "output_dir": relative,
                })
                if args.isolate_failed_slots:
                    disabled_slots.add(slot)
                    if len(disabled_slots) == 6:
                        stop = True
                else:
                    stop = True
            else:
                completed.append(row)
        if stop and active:
            # Existing chunks are allowed to finish; no unstarted task is selected
            # after an infrastructure stop.
            continue
        if stop and not active:
            break

    (output / "parallel_manifest.json").write_text(json.dumps({
        "schema_version": 1, "scheduler": "dynamic_fixed_chunks",
        "segment": args.segment, "model": "glm-5.2", "workers": 6,
        "chunk_size": args.chunk_size, "shards": manifest_rows,
    }, indent=2, sort_keys=True) + "\n")
    statuses = []
    for row in completed:
        statuses.append(json.loads((row["output_dir"] / "status.json").read_text()))
    for failure in failures:
        path = ROOT / failure["output_dir"] / "status.json"
        if path.is_file():
            statuses.append(json.loads(path.read_text()))
    summary = {
        "status": "completed" if not failures and len(completed) == len(planned) else "partial",
        "segment": args.segment, "planned_chunks": len(planned),
        "completed_chunks": len(completed), "unstarted_chunks": len(pending),
        "episodes_completed": sum(int(row.get("episodes_completed", 0)) for row in statuses),
        "task_calls": sum(int(row.get("task_calls", 0)) for row in statuses),
        "successful_provider_responses": sum(int(row.get("successful_provider_responses", 0)) for row in statuses),
        "input_tokens": sum(int(row.get("input_tokens", 0)) for row in statuses),
        "output_tokens": sum(int(row.get("output_tokens", 0)) for row in statuses),
        "server_model_identifiers": sorted({value for row in statuses for value in row.get("server_model_identifiers", [])}),
        "failures": failures, "elapsed_seconds": time.monotonic() - started,
        "disabled_slots": sorted(disabled_slots),
        "peak_rss_before_kib": rss_before,
        "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "available_after_bytes": effective_available_bytes(),
    }
    (output / "status.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
