"""Run the frozen post-run Probe-then-RAG baseline only."""
from __future__ import annotations

import argparse
import json
import resource
import time
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path

from arbiter.checkpoint import effective_available_bytes, require_memory
from arbiter.evaluation.main_matrix import MAIN_CELLS, READING_MODES, SEEDS
from arbiter.evaluation.p6_execution import run_matrix
from arbiter.evaluation.p6_matrix import P6Job
from arbiter.evaluation.reporting import load_episode_records, validate_conflicts
from arbiter.pinning import verify_webcraft_pin
from run_main import load_evaluation_tasks


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--reading-mode", choices=READING_MODES, required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/post_run_probe_then_rag"),
    )
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--index-dir")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    webcraft = args.webcraft_root.resolve()
    tasks = load_evaluation_tasks(
        args.tasks, webcraft / "config/resource_model.json",
    )
    jobs = [
        P6Job(condition, task["id"], world, "probe_then_rag", seed)
        for condition, world in MAIN_CELLS
        for task in tasks
        for seed in SEEDS
    ]
    if len(jobs) != 748:
        raise AssertionError("post-run baseline matrix must contain 748 episodes")
    mode_dir = args.output_dir / args.reading_mode
    mode_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "status": "post_run_baseline_addition",
        "method": "probe_then_rag",
        "reading_mode": args.reading_mode,
        "cells": [list(cell) for cell in MAIN_CELLS],
        "seeds": list(SEEDS), "episodes": len(jobs),
        "jobs": [asdict(job) for job in jobs],
    }
    (mode_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n",
    )
    verify_webcraft_pin(
        ROOT / "config/webcraft_pin.json", webcraft, require_ready=True,
    )
    available_before = require_memory(512 * 1024**2, reserve_bytes=2 * 1024**3)
    started = time.monotonic()
    episodes = run_matrix(
        jobs, tasks, root=webcraft,
        pin_path=ROOT / "config/webcraft_pin.json",
        index_dir=args.index_dir or str(webcraft / "corpus/indexes/formal_p1"),
        output_dir=mode_dir, top_k=10, reading_mode=args.reading_mode,
    )
    records = load_episode_records(episodes)
    validate_conflicts(records, webcraft / "data/conflicts.jsonl")
    if len(records) != 748 or {row.method for row in records} != {"probe_then_rag"}:
        raise RuntimeError("post-run baseline output is incomplete")
    record = {
        "schema_version": 1,
        "finished_at": datetime.now(timezone.utc).isoformat(),
        "reading_mode": args.reading_mode,
        "episodes": len(records),
        "elapsed_seconds": time.monotonic() - started,
        "effective_available_before": available_before,
        "effective_available_after": effective_available_bytes(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    (mode_dir / "resource_usage.json").write_text(
        json.dumps(record, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
