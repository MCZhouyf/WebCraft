"""Run the one-shot frozen shared-shell ablation on development tasks."""
from __future__ import annotations

import csv
import hashlib
import json
from dataclasses import asdict
from pathlib import Path

from arbiter.evaluation.p6_execution import run_matrix
from arbiter.evaluation.p6_matrix import (
    P6_CONDITIONS, P6_SEEDS, P6_WORLDS, P6Job, development_tasks,
    validate_coverage,
)
from arbiter.evaluation.reporting import load_episode_records
from arbiter.pinning import verify_webcraft_pin


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")
METHODS = ("arbiter_shell_naive", "arbiter")


def _write_csv(path: Path, rows: list[dict]) -> None:
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]), lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def _mean(rows: list[dict], key: str) -> float:
    return sum(float(row["metrics"][key]) for row in rows) / len(rows)


def main() -> None:
    output = ROOT / "artifacts/p7_shell_ablation"
    output.mkdir(parents=True, exist_ok=True)
    pin = verify_webcraft_pin(
        ROOT / "config/webcraft_pin.json", WEBCRAFT, require_ready=True,
    )
    tasks = development_tasks(
        ROOT / "config/p6_development_tasks.json",
        WEBCRAFT / "config/resource_model.json",
    )
    jobs = [
        P6Job(condition, task["id"], world, method, seed)
        for condition in P6_CONDITIONS
        for task in tasks
        for world in P6_WORLDS
        for method in METHODS
        for seed in P6_SEEDS
    ]
    if len(jobs) != 144:
        raise AssertionError("shell ablation must contain exactly 144 episodes")
    manifest = {
        "schema_version": 1,
        "scope": "frozen development shell ablation",
        "methods": list(METHODS),
        "episodes": len(jobs),
        "jobs": [asdict(job) for job in jobs],
    }
    manifest_path = output / "run_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")
    episode_path = run_matrix(
        jobs, tasks, root=WEBCRAFT,
        pin_path=ROOT / "config/webcraft_pin.json",
        index_dir=str(WEBCRAFT / "corpus/indexes/formal"),
        output_dir=output, top_k=10, reading_mode="all_public",
    )
    records = load_episode_records(episode_path)
    validate_coverage(jobs, records)
    raw = [json.loads(line) for line in episode_path.read_text().splitlines() if line]
    frozen = [
        json.loads(line)
        for line in (ROOT / "artifacts/p6_final_all_public/episodes.jsonl").read_text().splitlines()
        if line
    ]
    groups = {
        "naive_rag": [row for row in frozen if row["method"] == "naive_rag"],
        **{method: [row for row in raw if row["method"] == method] for method in METHODS},
    }
    table = []
    for method in ("naive_rag", "arbiter_shell_naive", "arbiter"):
        rows = groups[method]
        if len(rows) != 72:
            raise RuntimeError(f"{method} must have 72 development episodes")
        table.append({
            "method": method,
            "episodes": len(rows),
            "SR": _mean(rows, "SR"),
            "HTR_commit": _mean(rows, "HTR_commit"),
            "HTR_all": _mean(rows, "HTR_all"),
            "probe_count": _mean(rows, "probe_count"),
            "probe_cost": _mean(rows, "probe_cost"),
            "primary_cost": _mean(rows, "primary_cost"),
            "result_source": "frozen_reference" if method == "naive_rag" else "shared_shell_run",
        })
    _write_csv(output / "aggregate.csv", table)
    shell = next(row for row in table if row["method"] == "arbiter_shell_naive")
    arbiter = next(row for row in table if row["method"] == "arbiter")
    audit = {
        "schema_version": 1,
        "post_selection_development_result": True,
        "tuning_runs": 0,
        "evaluation_tasks_read_or_run": False,
        "llm_calls": 0,
        "webcraft_commit": pin["webcraft_commit"],
        "episodes": len(raw),
        "methods": list(METHODS),
        "episode_sha256": hashlib.sha256(episode_path.read_bytes()).hexdigest(),
        "manifest_sha256": hashlib.sha256(manifest_path.read_bytes()).hexdigest(),
        "shell_minus_arbiter_HTR_commit": shell["HTR_commit"] - arbiter["HTR_commit"],
        "aggregate": table,
    }
    (output / "audit.json").write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n")
    print(json.dumps(audit, sort_keys=True))


if __name__ == "__main__":
    main()
