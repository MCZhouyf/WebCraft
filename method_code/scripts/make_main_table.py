import argparse
from pathlib import Path

from arbiter.checkpoint import require_memory
from arbiter.evaluation.reporting import (
    load_episode_records,
    validate_conflicts,
    write_main_table,
)
from arbiter.evaluation.p6_matrix import coverage_conflict_ids, result_subsets


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument(
        "--conflicts", type=Path,
        default=Path("/root/autodl-tmp/webcraft/data/conflicts.jsonl"),
    )
    parser.add_argument("--bootstrap", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=2026)
    parser.add_argument(
        "--subset", choices=("all", "two_sided", "one_sided"), default="all",
    )
    parser.add_argument(
        "--coverage", type=Path,
        default=Path("/root/autodl-tmp/webcraft/reports/corpus_stats/coverage_p1.csv"),
    )
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    require_memory(512 * 1024**2)
    episodes = load_episode_records(args.input)
    validate_conflicts(episodes, args.conflicts)
    episodes = result_subsets(
        episodes, coverage_conflict_ids(args.coverage, "one_sided"),
    )[args.subset]
    rows = write_main_table(
        episodes, args.output_dir, iterations=args.bootstrap, seed=args.seed,
    )
    print(
        f"wrote {len(rows)} method-condition rows from {len(episodes)} "
        f"{args.subset} episodes"
    )
