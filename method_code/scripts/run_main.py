"""Prepare, time one cell, or run the frozen ARBITER main experiment."""
from __future__ import annotations

import argparse
import hashlib
import json
import resource
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from arbiter.agent.aggregation import operator_from_assertion
from arbiter.checkpoint import effective_available_bytes, require_memory
from arbiter.evaluation.main_matrix import (
    MAIN_CELLS,
    METHOD_SETS,
    READING_MODES,
    build_main_matrix,
    write_main_manifest,
)
from arbiter.evaluation.p6_execution import run_matrix
from arbiter.evaluation.reporting import load_episode_records, validate_conflicts, write_main_table
from arbiter.normalization import mechanism_id
from arbiter.pinning import verify_webcraft_pin


ROOT = Path(__file__).resolve().parents[1]
WEBCRAFT = Path("/root/autodl-tmp/webcraft")


def _read_rows(path: Path) -> list[dict[str, Any]]:
    if path.suffix == ".json":
        rows = json.loads(path.read_text())
    else:
        rows = [json.loads(line) for line in path.read_text().splitlines() if line.strip()]
    if not isinstance(rows, list):
        raise ValueError("evaluation task source must contain a list of records")
    return rows


def load_evaluation_tasks(path: Path, resource_model: Path) -> list[dict[str, Any]]:
    rows = [
        row for row in _read_rows(path)
        if str(row.get("split", "")).casefold() in {"eval", "evaluation", "test"}
    ]
    if len(rows) != 34 or len({str(row.get("id")) for row in rows}) != 34:
        raise ValueError("task source must expose exactly 34 evaluation-split records")
    standard = json.loads(resource_model.read_text())["standard_inventory"]
    projected = []
    for row in rows:
        required = {"id", "target", "feasible", "conflict_ids"}
        if not required <= set(row):
            raise ValueError(f"evaluation task lacks public execution fields: {row.get('id')}")
        projected.append({
            "id": str(row["id"]), "target": row["target"],
            "feasible": row["feasible"], "conflict_ids": row["conflict_ids"],
            "standard_items": standard,
            "task_supplements": row.get("task_supplements", []),
        })
    return sorted(projected, key=lambda row: row["id"])


def load_prior_bundle(path: Path) -> tuple[dict[str, Any], str]:
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    payload = json.loads(path.read_text())
    if set(payload) != {"schema_version", "assertions", "samples"}:
        raise ValueError("frozen prior bundle must contain schema_version/assertions/samples")
    if payload["schema_version"] != 1 or not payload["samples"]:
        raise ValueError("invalid or empty frozen prior bundle")
    operators = []
    for assertion in payload["assertions"]:
        operator = operator_from_assertion(assertion)
        if operator.mechanism_id != mechanism_id({
            key: value for key, value in assertion.items() if key != "_execution"
        }):
            raise ValueError("prior assertion identity mismatch")
        operators.append(operator)
    universe = {operator.mechanism_id for operator in operators}
    if not universe or any(set(sample) != universe for sample in payload["samples"]):
        raise ValueError("prior samples do not exactly cover prior operator universe")
    return {"operators": operators, "samples": payload["samples"]}, digest


def _cell(value: str) -> tuple[str, str]:
    for condition, world in MAIN_CELLS:
        if value == f"{condition}:{world}":
            return condition, world
    raise argparse.ArgumentTypeError("cell must be one preregistered condition:world pair")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-mode", choices=("prepare", "estimate", "full"), default="prepare")
    parser.add_argument(
        "--reading-mode", choices=("both", *READING_MODES), default="both",
        help="run both frozen reading modes or one mode as an independent shard",
    )
    parser.add_argument("--method-set", choices=tuple(METHOD_SETS), required=True)
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--prior-samples", type=Path)
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/main"))
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument("--index-dir")
    parser.add_argument("--estimate-cell", type=_cell, default=MAIN_CELLS[0])
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    webcraft = args.webcraft_root.resolve()
    tasks = load_evaluation_tasks(args.tasks, webcraft / "config/resource_model.json")
    all_jobs = build_main_matrix((row["id"] for row in tasks), args.method_set)
    selected_cells = (args.estimate_cell,) if args.run_mode == "estimate" else MAIN_CELLS
    reading_modes = READING_MODES if args.reading_mode == "both" else (args.reading_mode,)
    jobs = build_main_matrix(
        (row["id"] for row in tasks), args.method_set, cells=selected_cells,
    )
    for reading_mode in reading_modes:
        write_main_manifest(
            jobs, args.output_dir / reading_mode / "run_manifest.json",
            method_set=args.method_set, reading_mode=reading_mode,
        )
    if args.run_mode == "prepare":
        print(json.dumps({
            "status": "PREPARED_NOT_RUN", "full_episodes_per_reading_mode": len(all_jobs),
            "selected_episodes_per_reading_mode": len(jobs),
            "reading_modes": list(reading_modes), "method_set": args.method_set,
        }, sort_keys=True))
        return 0
    if "parametric" in METHOD_SETS[args.method_set] and args.prior_samples is None:
        raise RuntimeError("main execution requires --prior-samples for parametric")
    prior_bundle, prior_hash = load_prior_bundle(args.prior_samples)
    verify_webcraft_pin(ROOT / "config/webcraft_pin.json", webcraft, require_ready=True)
    available_before = require_memory(512 * 1024**2, reserve_bytes=2 * 1024**3)
    started = time.monotonic()
    for reading_mode in reading_modes:
        mode_dir = args.output_dir / reading_mode
        episodes = run_matrix(
            jobs, tasks, root=webcraft, pin_path=ROOT / "config/webcraft_pin.json",
            index_dir=args.index_dir or str(webcraft / "corpus/indexes/formal_p1"),
            output_dir=mode_dir, top_k=10, reading_mode=reading_mode,
            prior_bundle=prior_bundle,
        )
        records = load_episode_records(episodes)
        validate_conflicts(records, webcraft / "data/conflicts.jsonl")
        write_main_table(records, mode_dir / "tables")
    elapsed = time.monotonic() - started
    projected = elapsed * len(all_jobs) / len(jobs)
    resource_record = {
        "schema_version": 1, "finished_at": datetime.now(timezone.utc).isoformat(),
        "run_mode": args.run_mode, "method_set": args.method_set,
        "reading_modes": list(reading_modes),
        "prior_samples_sha256": prior_hash, "elapsed_seconds": elapsed,
        "projected_full_seconds": projected,
        "effective_available_before": available_before,
        "effective_available_after": effective_available_bytes(),
        "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    timing_name = (
        "timing.json" if args.reading_mode == "both"
        else f"timing_{args.reading_mode}.json"
    )
    (args.output_dir / timing_name).write_text(
        json.dumps(resource_record, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(resource_record, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
