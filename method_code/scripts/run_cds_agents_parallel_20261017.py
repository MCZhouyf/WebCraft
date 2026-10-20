"""Run the preregistered A8 matrix with six fixed-key workers."""
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
from run_cds_agents_20261017 import WEBCRAFT, all_jobs


ROOT = Path(__file__).resolve().parents[1]
PRIVATE_CONFIG = Path("/root/.config/scope_credentials/glm52_pending.json")


def args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--credential-config", type=Path, default=PRIVATE_CONFIG)
    parser.add_argument("--request-interval", type=float, default=2.0)
    return parser.parse_args()


def main() -> int:
    parsed = args()
    output = parsed.output_dir.resolve()
    if output.exists() and any(output.iterdir()):
        raise RuntimeError("parallel output directory must be empty")
    if effective_available_bytes() < int(1.8 * 1024**3):
        raise RuntimeError("effective free memory below authorized 1.8 GiB threshold")
    config = json.loads(parsed.credential_config.read_text())
    keys = list(dict.fromkeys(config.get("api_keys", [])))
    if config.get("model") != "glm-5.2" or len(keys) < 6:
        raise RuntimeError("six unique glm-5.2 credentials are required")
    jobs = all_jobs(WEBCRAFT)
    shards = [jobs[index::6] for index in range(6)]
    if sorted(job["instance_id"] + job["method"] + job["condition"] for shard in shards for job in shard) != sorted(
            job["instance_id"] + job["method"] + job["condition"] for job in jobs):
        raise RuntimeError("six-worker partition changed A8 job coverage")
    output.mkdir(parents=True)
    handles = []
    processes = []
    manifest = {"schema_version": 1, "scheduler": "six_fixed_key_workers", "workers": 6,
                "episodes": len(jobs), "shards": []}
    for slot, shard in enumerate(shards, 1):
        worker = output / f"worker_{slot}"
        worker.mkdir()
        jobs_path = worker / "jobs.json"
        jobs_path.write_text(json.dumps(shard, indent=2) + "\n")
        log_path = worker / "worker.log"
        env = os.environ.copy()
        env.update({
            "WC_LLM_API_KEY": keys[slot - 1], "WC_LLM_BASE_URL": str(config["base_url"]),
            "WC_LLM_MODEL": "glm-5.2", "ARB_LLM_BUDGET": "1000000",
            "WC_LLM_PRICE_IN_PER_MTOK": "8", "WC_LLM_PRICE_CACHED_IN_PER_MTOK": "2",
            "WC_LLM_PRICE_OUT_PER_MTOK": "28", "WC_LLM_CURRENCY": "CNY",
            "CUDA_VISIBLE_DEVICES": "", "OMP_NUM_THREADS": "1", "OPENBLAS_NUM_THREADS": "1",
            "MKL_NUM_THREADS": "1", "PYTHONPATH": f"{ROOT / 'src'}:{ROOT / 'scripts'}",
        })
        env.pop("WC_LLM_API_KEYS", None)
        command = [
            "nice", "-n", "15", sys.executable, str(ROOT / "scripts/run_cds_agents_20261017.py"),
            "--jobs-file", str(jobs_path), "--output-dir", str(worker / "run"),
            "--ledger", str(worker / "run/ledger.jsonl"),
            "--request-interval", str(parsed.request_interval),
        ]
        handle = log_path.open("w")
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdout=handle,
                                   stderr=subprocess.STDOUT, text=True)
        handles.append(handle)
        processes.append(process)
        manifest["shards"].append({"slot": slot, "episodes": len(shard),
                                   "jobs_file": str(jobs_path.relative_to(ROOT))})
    (output / "parallel_manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    started = time.monotonic()
    rss_before = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    codes = [process.wait() for process in processes]
    for handle in handles:
        handle.close()
    summary = {"status": "completed" if all(code == 0 for code in codes) else "partial",
               "worker_exit_codes": codes, "elapsed_seconds": time.monotonic() - started,
               "peak_rss_before_kib": rss_before,
               "peak_rss_after_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
               "available_after_bytes": effective_available_bytes()}
    (output / "parallel_status.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
    print(json.dumps(summary, sort_keys=True))
    return 0 if all(code == 0 for code in codes) else 2


if __name__ == "__main__":
    raise SystemExit(main())
