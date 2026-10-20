"""Evaluate public chunk claims against revision-matched private gold claims."""
from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def source_key(source: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(source.get("source_site", source.get("site"))),
        str(source["page_title"]),
        int(source["revid"]),
    )


def structured_signature(claim: dict[str, Any]) -> tuple[Any, ...]:
    kind = claim["claim_type"]
    if kind == "chest_loot":
        return (kind, claim["subject_item"], claim["polarity"], claim["tense"])
    payload = claim["payload"]
    return (
        kind,
        claim["subject_item"],
        claim["polarity"],
        claim["tense"],
        int(payload.get("output_count", 1)),
        payload.get("station"),
        tuple(sorted(payload.get("ingredients", []))),
    )


def historical_drop_signature(claim: dict[str, Any]) -> tuple[Any, ...]:
    return (
        "historical_drop",
        claim["subject_item"],
        claim["polarity"],
        "historical",
    )


def score(
    gold: set[tuple[Any, ...]], predicted: set[tuple[Any, ...]]
) -> dict[str, int | float]:
    matched = len(gold & predicted)
    precision = matched / len(predicted) if predicted else 0.0
    recall = matched / len(gold) if gold else 0.0
    return {
        "gold": len(gold),
        "predicted": len(predicted),
        "matched": matched,
        "accuracy": precision,
        "recall": recall,
        "f1": 2 * precision * recall / (precision + recall)
        if precision + recall
        else 0.0,
    }


def evaluate_cache(
    documents_path: Path, cache_path: Path, gold_paths: Iterable[Path]
) -> dict[str, Any]:
    documents = read_jsonl(documents_path)
    chunks = {row["chunk_id"]: source_key(row) for row in documents}

    predicted_structured: set[tuple[Any, ...]] = set()
    predicted_drop: set[tuple[Any, ...]] = set()
    excluded_current_drops = 0
    for row in read_jsonl(cache_path):
        revision = chunks[row["chunk_id"]]
        for claim in row["claims"]:
            kind = claim["claim_type"]
            if kind in {"recipe", "smelting", "chest_loot"} and claim["tense"] == "current":
                predicted_structured.add((revision, structured_signature(claim)))
            elif kind == "drop" and claim["tense"] == "historical":
                predicted_drop.add((revision, historical_drop_signature(claim)))
            elif kind == "drop":
                excluded_current_drops += 1

    gold_structured: set[tuple[Any, ...]] = set()
    gold_drop: set[tuple[Any, ...]] = set()
    for path in gold_paths:
        for claim in read_jsonl(path):
            revision = source_key(claim["source"])
            kind = claim["claim_type"]
            if kind in {"recipe", "smelting", "chest_loot"} and claim["tense"] == "current":
                gold_structured.add((revision, structured_signature(claim)))
            elif kind == "historical_drop":
                gold_drop.add((revision, historical_drop_signature(claim)))

    combined_gold = {("structured", value) for value in gold_structured} | {
        ("historical_drop", value) for value in gold_drop
    }
    combined_predicted = {
        ("structured", value) for value in predicted_structured
    } | {("historical_drop", value) for value in predicted_drop}
    return {
        "structured_recipe_smelting_loot": score(
            gold_structured, predicted_structured
        ),
        "historical_drop_subject_level": score(gold_drop, predicted_drop),
        "combined_comparable": score(combined_gold, combined_predicted),
        "excluded_current_drop_claims_without_structured_gold": excluded_current_drops,
        "notes": (
            "Recipes and smelting are matched by revision, subject, polarity, tense, "
            "output count, station, and ingredient multiset; chest loot is matched by "
            "revision and subject. Historical-drop gold stores prose rather than a "
            "structured drop payload, so that group is matched at subject/polarity level."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--documents",
        type=Path,
        default=ROOT / "corpus/indexes/formal/mixed/documents.jsonl",
    )
    parser.add_argument(
        "--cache",
        type=Path,
        default=ROOT / "corpus/claims_cache/mixed/chunk_claims.jsonl",
    )
    parser.add_argument(
        "--gold-dir", type=Path, default=ROOT / "corpus/private"
    )
    args = parser.parse_args()
    gold_paths = sorted(args.gold_dir.glob("claims_gold_*.jsonl"))
    print(
        json.dumps(
            evaluate_cache(args.documents, args.cache, gold_paths),
            indent=2,
            sort_keys=True,
        )
    )


if __name__ == "__main__":
    main()
