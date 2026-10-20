"""Build the preregistered 100-row manual review sample for prose claims."""
from __future__ import annotations

import argparse
import csv
import json
import random
from pathlib import Path
from typing import Any

from corpus.extract_claims import _json_object
from corpus.llm_client import BudgetedLLMClient


PROMPT = """Review historical Minecraft Wiki excerpts. Return JSON only as
{"reviews":[...]}. Produce exactly one result for every review_id. Each result has:
review_id (copied exactly), is_factual_claim (boolean), claim_type
(historical_recipe, historical_drop, or other), subject_item (canonical lowercase
snake_case ID or null), tense (historical, current, or future), polarity (positive or
negative), and summary (one short factual sentence). Use only the excerpt; do not add
outside facts and do not judge whether the claim is true in any game version."""


def deterministic_sample(rows: list[dict[str, Any]], count: int, seed: int) -> list[dict[str, Any]]:
    candidates = [
        row for row in rows
        if row["claim_type"] in {"historical_recipe", "historical_drop"}
        and row["tense"] == "historical"
    ]
    if len(candidates) < count:
        raise ValueError("not enough historical claims for review")
    return random.Random(seed).sample(candidates, count)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--gold-dir", type=Path, required=True)
    parser.add_argument("--private-output", type=Path, required=True)
    parser.add_argument("--csv-output", type=Path, required=True)
    args = parser.parse_args()
    sampled = []
    for index, tag in enumerate(("t1", "t2")):
        rows = [
            json.loads(line)
            for line in (args.gold_dir / f"claims_gold_{tag}.jsonl").read_text().splitlines()
        ]
        for row in deterministic_sample(rows, 50, 20261006 + index):
            row = dict(row)
            row["review_id"] = f"review_{len(sampled) + 1:03d}"
            sampled.append(row)

    progress_path = args.private_output.with_suffix(".progress.json")
    completed = {}
    if args.private_output.exists():
        completed = {
            row["review_id"]: row
            for row in map(json.loads, args.private_output.read_text().splitlines())
        }
    client = BudgetedLLMClient()
    try:
        pending = [row for row in sampled if row["review_id"] not in completed]
        for start in range(0, len(pending), 20):
            batch = pending[start : start + 20]
            payload = [
                {
                    "review_id": row["review_id"],
                    "page_title": row["source"]["page_title"],
                    "excerpt": row["payload"]["text"][:1600],
                }
                for row in batch
            ]
            content = client.complete(
                [
                    {"role": "system", "content": PROMPT},
                    {"role": "user", "content": json.dumps(payload, ensure_ascii=False)},
                ],
                temperature=0.0,
                max_tokens=5000,
                purpose="a2_historical_review",
            )
            values = _json_object(content).get("reviews", [])
            returned = {
                value.get("review_id"): value
                for value in values
                if isinstance(value, dict) and isinstance(value.get("review_id"), str)
            }
            expected = {row["review_id"] for row in batch}
            if set(returned) != expected:
                raise ValueError("review response IDs do not match batch")
            completed.update(returned)
            args.private_output.parent.mkdir(parents=True, exist_ok=True)
            args.private_output.write_text(
                "".join(json.dumps(completed[key], sort_keys=True) + "\n" for key in sorted(completed))
            )
            progress_path.write_text(
                json.dumps({"completed": len(completed), "total": 100}, indent=2) + "\n"
            )
    finally:
        client.close()

    args.csv_output.parent.mkdir(parents=True, exist_ok=True)
    fields = (
        "review_id", "timepoint", "page_title", "revid", "source_text",
        "auto_is_factual_claim", "auto_claim_type", "auto_subject_item",
        "auto_tense", "auto_polarity", "auto_summary", "human_correct", "human_notes",
    )
    with args.csv_output.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        for source in sampled:
            auto = completed[source["review_id"]]
            writer.writerow(
                {
                    "review_id": source["review_id"],
                    "timepoint": source["source"]["timepoint"],
                    "page_title": source["source"]["page_title"],
                    "revid": source["source"]["revid"],
                    "source_text": source["payload"]["text"][:1600],
                    "auto_is_factual_claim": auto.get("is_factual_claim"),
                    "auto_claim_type": auto.get("claim_type"),
                    "auto_subject_item": auto.get("subject_item"),
                    "auto_tense": auto.get("tense"),
                    "auto_polarity": auto.get("polarity"),
                    "auto_summary": auto.get("summary"),
                    "human_correct": "",
                    "human_notes": "",
                }
            )
    print(json.dumps({"review_rows": len(sampled), "completed": len(completed)}))


if __name__ == "__main__":
    main()
