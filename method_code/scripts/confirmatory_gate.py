"""Apply the frozen two-part confirmatory decision to all-public episodes."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

from arbiter.evaluation.confirmatory import confirmatory_decision
from arbiter.evaluation.reporting import load_episode_records


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = load_episode_records(args.input)
    result = confirmatory_decision(records)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")
    print(json.dumps(result, sort_keys=True))


if __name__ == "__main__":
    main()
