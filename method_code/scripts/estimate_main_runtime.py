"""Time one preregistered main-grid cell using development tasks only."""
from __future__ import annotations

import argparse
import json
import resource
import time
from dataclasses import asdict
from pathlib import Path

from arbiter.evaluation.main_matrix import METHOD_SETS
from arbiter.evaluation.p6_execution import run_matrix
from arbiter.evaluation.p6_matrix import P6Job, development_tasks
from arbiter.pinning import verify_webcraft_pin

from run_main import load_prior_bundle


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
CELL = ("matched_1.19", "1.19")
SEED = 20261006
FULL_EPISODES = 34 * 11 * len(METHOD_SETS["baselines_plus_arbiter"]) * 2 * 2


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--prior-samples", type=Path,
        default=Path("artifacts/prior/frozen_prior_bundle.json"),
    )
    parser.add_argument(
        "--output-dir", type=Path,
        default=Path("artifacts/p7_main_runtime_estimate"),
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    pin = verify_webcraft_pin(
        ROOT / "config/webcraft_pin.json", WEBCRAFT, require_ready=True,
    )
    tasks = development_tasks(
        ROOT / "config/p6_development_tasks.json",
        WEBCRAFT / "config/resource_model.json",
    )
    methods = METHOD_SETS["baselines_plus_arbiter"]
    jobs = [
        P6Job(CELL[0], task["id"], CELL[1], method, SEED)
        for task in tasks for method in methods
    ]
    prior, prior_hash = load_prior_bundle(args.prior_samples)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    manifest = {
        "schema_version": 1,
        "scope": "development-only runtime estimate",
        "evaluation_tasks_read_or_run": False,
        "reading_mode": "all_public",
        "cell": list(CELL),
        "seed": SEED,
        "methods": list(methods),
        "episodes": len(jobs),
        "jobs": [asdict(job) for job in jobs],
        "webcraft_commit": pin["webcraft_commit"],
        "prior_bundle_sha256": prior_hash,
    }
    (args.output_dir / "run_manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True) + "\n"
    )
    started = time.monotonic()
    episodes = run_matrix(
        jobs, tasks, root=WEBCRAFT,
        pin_path=ROOT / "config/webcraft_pin.json",
        index_dir=str(WEBCRAFT / "corpus/indexes/formal_p1"),
        output_dir=args.output_dir, top_k=10, reading_mode="all_public",
        prior_bundle=prior,
    )
    elapsed = time.monotonic() - started
    result = {
        **manifest,
        "episode_path": str(episodes),
        "elapsed_seconds": elapsed,
        "seconds_per_episode": elapsed / len(jobs),
        "projected_full_episodes": FULL_EPISODES,
        "projected_full_seconds": elapsed / len(jobs) * FULL_EPISODES,
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        "llm_calls": 0,
    }
    (args.output_dir / "timing.json").write_text(
        json.dumps(result, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
