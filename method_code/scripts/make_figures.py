import argparse
from pathlib import Path

from arbiter.checkpoint import require_memory
from arbiter.evaluation.figures import plot_contextladder, plot_htr_probes


def parse_args():
    parser = argparse.ArgumentParser()
    parser.add_argument("--main-table", type=Path, required=True)
    parser.add_argument(
        "--context-grid", type=Path,
        default=Path("experiments/contextladder/results/grid_results.csv"),
    )
    parser.add_argument("--output-dir", type=Path, required=True)
    return parser.parse_args()


if __name__ == "__main__":
    args = parse_args()
    require_memory(1024 * 1024**2)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    plot_htr_probes(args.main_table, args.output_dir / "figure2_htr_probes.png")
    plot_contextladder(
        args.context_grid, args.output_dir / "figure3_contextladder.png",
        args.output_dir / "figure3_slice.csv",
    )
    print(f"wrote figures to {args.output_dir}")
