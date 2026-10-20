"""Count effective extractor routes in every frozen public claim cache."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any

from corpus.solvability_audit import llm_extractor_version


ROOT = Path(__file__).resolve().parents[1]
CACHE_ROOTS = {
    "formal": Path("corpus/claims_cache"),
    "formal_p1": Path("corpus/claims_cache/formal_p1"),
}


def effective_extractor(claim: dict[str, Any], *, llm_version: str) -> str:
    marker = claim.get("payload", {}).get("extractor")
    if isinstance(marker, str) and marker:
        return marker
    if claim.get("extractor_version") == llm_version:
        return "llm"
    return "unclassified"


def count_caches(root: Path) -> list[dict[str, Any]]:
    llm_version = llm_extractor_version(root)
    rows = []
    for generation, relative in CACHE_ROOTS.items():
        cache_root = root / relative
        aggregate: Counter[str] = Counter()
        for directory in sorted(path for path in cache_root.iterdir() if path.is_dir()):
            cache_path = directory / "chunk_claims.jsonl"
            if not cache_path.is_file():
                continue
            counts: Counter[str] = Counter()
            for line in cache_path.read_text().splitlines():
                if not line.strip():
                    continue
                for claim in json.loads(line)["claims"]:
                    counts[effective_extractor(claim, llm_version=llm_version)] += 1
            aggregate.update(counts)
            rows.extend({
                "cache_generation": generation,
                "index_name": directory.name,
                "extractor": extractor,
                "claim_count": count,
            } for extractor, count in sorted(counts.items()))
        rows.extend({
            "cache_generation": generation,
            "index_name": "__all__",
            "extractor": extractor,
            "claim_count": count,
        } for extractor, count in sorted(aggregate.items()))
    return rows


def write_counts(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(
            handle,
            fieldnames=("cache_generation", "index_name", "extractor", "claim_count"),
            lineterminator="\n",
        )
        writer.writeheader()
        writer.writerows(rows)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, default=ROOT)
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("reports/corpus_stats/cache_extractor_counts.csv"),
    )
    args = parser.parse_args()
    root = args.root.resolve()
    output = args.output if args.output.is_absolute() else root / args.output
    rows = count_caches(root)
    write_counts(output, rows)
    print(json.dumps({"output": str(output), "rows": len(rows)}, sort_keys=True))


if __name__ == "__main__":
    main()
