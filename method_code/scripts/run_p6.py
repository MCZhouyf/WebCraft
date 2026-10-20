import argparse
import atexit
from datetime import datetime, timezone
import json
import os
import resource
from pathlib import Path

from arbiter.checkpoint import effective_available_bytes, require_memory
from arbiter.evaluation.p6_matrix import (
    build_matrix,
    development_tasks,
    judge_gate,
    one_sided_conflict_ids,
    result_subsets,
    validate_coverage,
    write_manifest,
)
from arbiter.evaluation.p6_execution import run_matrix
from arbiter.evaluation.reporting import (
    load_episode_records,
    validate_conflicts,
    write_main_table,
)
from arbiter.pinning import verify_webcraft_pin


WEBCRAFT = Path("/root/autodl-tmp/webcraft")


def parse_args():
    parser = argparse.ArgumentParser(
        description="Freeze the P6 matrix or score a complete schema-v2 run.",
    )
    parser.add_argument("--output-dir", type=Path, default=Path("artifacts/p6"))
    parser.add_argument("--episodes", type=Path)
    parser.add_argument("--prepare-only", action="store_true")
    parser.add_argument("--preview", action="store_true")
    parser.add_argument("--webcraft-root", type=Path, default=WEBCRAFT)
    parser.add_argument(
        "--development-tasks", type=Path,
        default=Path("config/p6_development_tasks.json"),
    )
    parser.add_argument(
        "--index-dir", default=None,
    )
    parser.add_argument("--top-k", type=int, default=10)
    parser.add_argument(
        "--reading-mode", choices=("all_public", "llm_only"),
        default=None,
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    webcraft = args.webcraft_root.resolve()
    index_dir = args.index_dir or str(webcraft / "corpus/indexes/formal")
    if args.preview:
        cap = float(os.environ.get("ARB_LLM_RUN_CAP", "15"))
        if cap > 15:
            raise RuntimeError("P6 preview ARB_LLM_RUN_CAP must not exceed 15 CNY")
        os.environ["ARB_LLM_RUN_CAP"] = str(cap)
    available_before = require_memory(
        512 * 1024**2, reserve_bytes=2 * 1024**3,
    )
    args.output_dir.mkdir(parents=True, exist_ok=True)
    resource_record = {
        "schema_version": 1,
        "started_at": datetime.now(timezone.utc).isoformat(),
        "pid": os.getpid(),
        "planned_bytes": 512 * 1024**2,
        "reserve_bytes": 2 * 1024**3,
        "effective_available_before": available_before,
        "peak_rss_kib_before": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
    }

    def write_resource_record() -> None:
        resource_record.update({
            "finished_at": datetime.now(timezone.utc).isoformat(),
            "effective_available_after": effective_available_bytes(),
            "peak_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
        })
        (args.output_dir / "resource_usage.json").write_text(
            json.dumps(resource_record, indent=2, sort_keys=True) + "\n",
        )

    atexit.register(write_resource_record)
    tasks = development_tasks(
        args.development_tasks, webcraft / "config/resource_model.json",
    )
    jobs = build_matrix(tasks)
    manifest = args.output_dir / "run_manifest.json"
    write_manifest(jobs, manifest)
    if args.prepare_only:
        print(f"prepared {len(jobs)} frozen jobs at {manifest}")
        raise SystemExit(0)

    verify_webcraft_pin(
        Path("config/webcraft_pin.json"), webcraft, require_ready=True,
        allow_preview=args.preview,
    )
    if args.episodes is None:
        args.episodes = run_matrix(
            jobs, tasks, root=webcraft, pin_path=Path("config/webcraft_pin.json"),
            index_dir=index_dir, output_dir=args.output_dir, top_k=args.top_k,
            preview=args.preview, reading_mode=args.reading_mode,
        )
    records = load_episode_records(args.episodes)
    validate_coverage(jobs, records)
    validate_conflicts(records, webcraft / "data/conflicts.jsonl")
    subsets = result_subsets(
        records, one_sided_conflict_ids(webcraft / "prereg/corpus_freeze.md"),
    )
    for label, subset in subsets.items():
        write_main_table(subset, args.output_dir / "tables" / label)
    selected_mode = args.reading_mode or "all_public"
    decision = judge_gate(records)
    decision["gate_applicable"] = selected_mode == "all_public"
    if not decision["gate_applicable"]:
        decision["descriptive_HTR_commit_decision"] = decision["decision"]
        decision["decision"] = "NOT_APPLICABLE"
    decision.update({
        "mode": "preview" if args.preview else "formal",
        "reading_mode": selected_mode,
        "subset_episodes": {name: len(rows) for name, rows in subsets.items()},
    })
    (args.output_dir / "gate.json").write_text(
        json.dumps(decision, indent=2, sort_keys=True) + "\n",
    )
    print(json.dumps(decision, sort_keys=True))
