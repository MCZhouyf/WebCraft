"""Build the no-truth runtime prior bundle from already frozen LLM samples."""
from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from arbiter.evaluation.prior_bundle import project_prior_bundle


WEBCRAFT_PRIOR = Path("/root/autodl-tmp/webcraft/corpus/llm_prior")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--unconditional", type=Path, default=WEBCRAFT_PRIOR / "samples.jsonl")
    parser.add_argument(
        "--assertions", type=Path,
        default=WEBCRAFT_PRIOR / "conditional_samples.jsonl",
    )
    parser.add_argument(
        "--output", type=Path,
        default=Path("artifacts/prior/frozen_prior_bundle.json"),
    )
    return parser.parse_args()


def digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main() -> None:
    args = parse_args()
    bundle = project_prior_bundle(args.unconditional, args.assertions)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(bundle, indent=2, sort_keys=True) + "\n")
    print(json.dumps({
        "output": str(args.output),
        "output_sha256": digest(args.output),
        "unconditional_sha256": digest(args.unconditional),
        "assertions_sha256": digest(args.assertions),
        "operators": len(bundle["assertions"]),
        "samples": len(bundle["samples"]),
        "llm_calls": 0,
    }, sort_keys=True))


if __name__ == "__main__":
    main()
