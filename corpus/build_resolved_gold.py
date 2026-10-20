"""Rebuild private deterministic claims from redirect-resolved snapshots."""
from __future__ import annotations

import json
from collections import Counter
from pathlib import Path

from corpus.gold_claims import (
    NameResolver,
    VERSIONS,
    extract_gold,
    normalized_truth_recipes,
    write_jsonl,
)


ROOT = Path(__file__).resolve().parents[1]
LABELS = {
    "T1": ("1.16.5", "claims_gold_t1.jsonl"),
    "T2": ("1.19", "claims_gold_t2.jsonl"),
    "T3": ("1.21.11", "claims_gold_t3.jsonl"),
    "current": ("current", "claims_gold_current.jsonl"),
    "Fandom2023": ("current", "claims_gold_provisional_2023.jsonl"),
}


def main() -> None:
    manifest = json.loads(
        (ROOT / "corpus/manifests/redirect_resolved_snapshots.json").read_text()
    )
    raw_root = ROOT / "corpus/raw/redirect_resolved"
    output_root = ROOT / "corpus/private"
    resolver = NameResolver()
    recipes = {version: normalized_truth_recipes(version) for version in VERSIONS}
    summary = {}
    for label, (timepoint, filename) in LABELS.items():
        claims = []
        for title, metadata in manifest[label].items():
            source = {
                "site": metadata["source_site"],
                "page_title": title,
                "revid": metadata["revid"],
                "timestamp": metadata["timestamp"],
                "timepoint": timepoint,
            }
            text = (raw_root / metadata["wikitext"]).read_text()
            claims.extend(extract_gold(text, source, resolver, recipes))
        write_jsonl(output_root / filename, claims)
        summary[label] = {
            "pages": len(manifest[label]),
            "claims": len(claims),
            "by_type": dict(Counter(claim["claim_type"] for claim in claims)),
        }
    print(json.dumps(summary, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
