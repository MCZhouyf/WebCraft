"""Launch six credential-isolated shards for one external-control segment."""
from __future__ import annotations

import argparse
import json
import os
import resource
import subprocess
import sys
import time
from pathlib import Path

from arbiter.checkpoint import effective_available_bytes


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_CONFIG = Path("/root/.config/scope_credentials/glm52_pending.json")
METHODS = ("direct_llm", "react")
COUNTS = {"oracle_knowledge": 110, "control_all_public": 60, "conditions": 132}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--segment", choices=tuple(COUNTS), required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--request-interval", type=float, default=2.0)
    parser.add_argument("--credential-config", type=Path, default=PRIVATE_CONFIG)
    return parser.parse_args()


def read_budget() -> str:
    for raw in (ROOT / "env/llm.env").read_text().splitlines():
        line = raw.strip().removeprefix("export ")
        if line.startswith("ARB_LLM_BUDGET="):
            return line.split("=", 1)[1].strip().strip('"').strip("'")
    return "1000000"


def split_three(total: int) -> list[tuple[int, int]]:
    base, extra = divmod(total, 3)
    sizes = [base + int(index < extra) for index in range(3)]
    offsets = []
    cursor = 0
    for size in sizes:
        offsets.append((cursor, size))
        cursor += size
    return offsets


def main() -> int:
    args = parse_args()
    output = args.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("parallel output directory must be empty")
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")
    config = json.loads(args.credential_config.read_text())
    keys = config.get("api_keys")
    if not isinstance(keys, list) or len(keys) < 6:
        raise RuntimeError("six private credentials are required")
    if config.get("model") != "glm-5.2":
        raise RuntimeError("private provider config is not locked to glm-5.2")
    output.mkdir(parents=True)
    budget = read_budget()
    processes = []
    shard_rows = []
    started = time.monotonic()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    slot = 0
    for method in METHODS:
        for offset, limit in split_three(COUNTS[args.segment]):
            slot += 1
            shard = output / f"worker_{slot}_{method}_{offset}_{limit}"
            log_path = output / f"worker_{slot}.log"
            env = os.environ.copy()
            env.update({
                "WC_LLM_API_KEY": keys[slot - 1],
                "WC_LLM_BASE_URL": str(config["base_url"]),
                "WC_LLM_MODEL": "glm-5.2",
                "ARB_LLM_BUDGET": budget,
                "WC_LLM_PRICE_IN_PER_MTOK": "0",
                "WC_LLM_PRICE_OUT_PER_MTOK": "0",
                "CUDA_VISIBLE_DEVICES": "",
                "OMP_NUM_THREADS": "1",
                "OPENBLAS_NUM_THREADS": "1",
                "MKL_NUM_THREADS": "1",
                "PYTHONPATH": str(ROOT / "src"),
            })
            env.pop("WC_LLM_API_KEYS", None)
            command = [
                "nice", "-n", "15", sys.executable,
                str(ROOT / "scripts/run_external_controls.py"),
                "--segment", args.segment,
                "--method", method,
                "--offset", str(offset),
                "--limit", str(limit),
                "--output-dir", str(shard),
                "--ledger", str(shard / "ledger.jsonl"),
                "--request-interval", str(args.request_interval),
            ]
            log_path.parent.mkdir(parents=True, exist_ok=True)
            handle = log_path.open("w")
            process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle,
                                       stderr=subprocess.STDOUT, text=True)
            processes.append((process, handle, slot, shard))
            shard_rows.append({
                "slot": slot, "method": method, "offset": offset, "limit": limit,
                "output_dir": str(shard.relative_to(ROOT)),
            })
            time.sleep(1)
    (output / "parallel_manifest.json").write_text(json.dumps({
        "schema_version": 1, "segment": args.segment, "workers": len(processes),
        "credential_slots": len(processes), "model": "glm-5.2",
        "request_interval_seconds": args.request_interval,
        "shards": shard_rows,
    }, indent=2, sort_keys=True) + "\n")
    failures = []
    for process, handle, slot, shard in processes:
        code = process.wait()
        handle.close()
        if code:
            failures.append({"slot": slot, "exit_code": code, "output_dir": str(shard)})
    statuses = []
    for _, _, slot, shard in processes:
        path = shard / "status.json"
        statuses.append(json.loads(path.read_text()) if path.is_file() else {
            "status": "missing", "slot": slot,
        })
    summary = {
        "status": "completed" if not failures and all(row.get("status") == "completed" for row in statuses) else "partial",
        "segment": args.segment,
        "workers": len(processes),
        "episodes_completed": sum(int(row.get("episodes_completed", 0)) for row in statuses),
        "task_calls": sum(int(row.get("task_calls", 0)) for row in statuses),
        "successful_provider_responses": sum(int(row.get("successful_provider_responses", 0)) for row in statuses),
        "input_tokens": sum(int(row.get("input_tokens", 0)) for row in statuses),
        "output_tokens": sum(int(row.get("output_tokens", 0)) for row in statuses),
        "server_model_identifiers": sorted({identifier for row in statuses for identifier in row.get("server_model_identifiers", [])}),
        "failures": failures,
        "elapsed_seconds": time.monotonic() - started,
        "peak_rss_before_kib": rss_before,
        "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "available_after_bytes": effective_available_bytes(),
    }
    (output / "status.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, indent=2, sort_keys=True))
    return 0 if summary["status"] == "completed" else 2


if __name__ == "__main__":
    raise SystemExit(main())
