"""Memory-gated six-slot scheduler for the preregistered A7 diagnostic matrix."""
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
WORKER_HEADROOM = 512 * 1024**2
BASE_HEADROOM = 2 * 1024**3


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--credential-config", type=Path, default=PRIVATE_CONFIG)
    parser.add_argument("--chunk-size", type=int, default=2)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--max-workers", type=int, default=6)
    return parser.parse_args()


def jobs(chunk_size: int) -> list[dict[str, Any]]:
    if chunk_size < 1:
        raise ValueError("chunk size must be positive")
    matrix = [("direct_llm", "K", 32), ("react", "K", 32), ("react", "K+C", 32), ("react", "K+N", 32)]
    scheduled = []
    for method, condition, count in matrix:
        for offset in range(0, count, chunk_size):
            scheduled.append({"method": method, "condition": condition, "offset": offset, "limit": min(chunk_size, count - offset)})
    # Interleave conditions so a single transient endpoint issue cannot bias one condition.
    return sorted(scheduled, key=lambda row: (row["offset"], row["method"], row["condition"]))


def launch(job: dict[str, Any], *, slot: int, sequence: int, output: Path, config: dict[str, Any], interval: float) -> dict[str, Any]:
    destination = output / f"chunk_{sequence:03d}_slot_{slot}_{job['method']}_{job['condition'].replace('+', 'plus')}_{job['offset']}"
    log_path = output / f"chunk_{sequence:03d}_slot_{slot}.log"
    env = os.environ.copy()
    env.update({
        "WC_LLM_API_KEY": config["api_keys"][slot - 1], "WC_LLM_BASE_URL": str(config["base_url"]), "WC_LLM_MODEL": "glm-5.2",
        "ARB_LLM_BUDGET": "1000000", "WC_LLM_PRICE_IN_PER_MTOK": "0", "WC_LLM_PRICE_OUT_PER_MTOK": "0",
        "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
        "PYTHONPATH": str(ROOT / "src"),
    })
    env.pop("WC_LLM_API_KEYS", None)
    command = ["nice", "-n", "15", sys.executable, str(ROOT / "scripts/run_diagnostic_controls_20261016.py"),
               "--method", job["method"], "--condition", job["condition"], "--offset", str(job["offset"]), "--limit", str(job["limit"]),
               "--output-dir", str(destination), "--ledger", str(destination / "ledger.jsonl"), "--request-interval", str(interval)]
    handle = log_path.open("w")
    process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle, stderr=subprocess.STDOUT, text=True)
    return {**job, "slot": slot, "sequence": sequence, "output_dir": destination, "log_path": log_path, "handle": handle, "process": process}


def main() -> int:
    args = parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("diagnostic output directory must be empty")
    config = json.loads(args.credential_config.read_text())
    if config.get("model") != "glm-5.2" or len(config.get("api_keys", [])) < 6:
        raise RuntimeError("six glm-5.2 credentials are required")
    if not 1 <= args.max_workers <= 6:
        raise ValueError("max workers must be between 1 and 6")
    output.mkdir(parents=True)
    planned = jobs(args.chunk_size)
    pending = deque(enumerate(planned, 1))
    active: dict[int, dict[str, Any]] = {}
    failures: list[dict[str, Any]] = []
    completed: list[dict[str, Any]] = []
    manifest = {"schema_version": 1, "scheduler": "a7_dynamic_six_slot", "model": "glm-5.2", "workers_max": args.max_workers,
                "chunk_size": args.chunk_size, "base_headroom_bytes": BASE_HEADROOM, "worker_headroom_bytes": WORKER_HEADROOM,
                "jobs": [{**job, "sequence": sequence, "output_dir": None} for sequence, job in enumerate(planned, 1)]}
    (output / "parallel_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    started = time.monotonic()
    before_rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    stop = False
    while pending or active:
        for slot in range(1, args.max_workers + 1):
            minimum = BASE_HEADROOM + WORKER_HEADROOM * (len(active) + 1)
            if stop or slot in active or not pending or effective_available_bytes() < minimum:
                continue
            sequence, job = pending.popleft()
            active[slot] = launch(job, slot=slot, sequence=sequence, output=output, config=config, interval=args.request_interval)
            time.sleep(0.5)
        time.sleep(1)
        for slot, row in list(active.items()):
            code = row["process"].poll()
            if code is None:
                continue
            row["handle"].close()
            del active[slot]
            relative = str(row["output_dir"].relative_to(ROOT))
            manifest["jobs"][row["sequence"] - 1]["output_dir"] = relative
            if code:
                failures.append({"slot": slot, "sequence": row["sequence"], "exit_code": code, "output_dir": relative})
                stop = True  # identity/error failures are global A7 stops.
            else:
                completed.append(row)
        if stop and not active:
            break
    statuses = []
    for row in completed:
        statuses.append(json.loads((row["output_dir"] / "status.json").read_text()))
    manifest["jobs_completed"] = len(completed)
    manifest["failures"] = failures
    (output / "parallel_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    summary = {"status": "completed" if not failures and len(completed) == len(planned) else "partial", "planned_chunks": len(planned),
               "completed_chunks": len(completed), "episodes_completed": sum(int(row.get("episodes_completed", 0)) for row in statuses),
               "task_calls": sum(int(row.get("task_calls", 0)) for row in statuses),
               "server_model_identifiers": sorted({item for row in statuses for item in row.get("server_model_identifiers", [])}),
               "failures": failures, "elapsed_seconds": time.monotonic() - started, "peak_rss_before_kib": before_rss,
               "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss, "available_after_bytes": effective_available_bytes()}
    (output / "status.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
