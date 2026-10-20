"""Compute development-conflict side coverage for the amended P0 rule."""
from __future__ import annotations

import argparse
import csv
import json
from collections import Counter
from pathlib import Path
from typing import Any


def subject(conflict: dict[str, Any]) -> str:
    assertion = conflict["assertion"]
    return assertion.get("output") or assertion["block"]


def side_matches(claim: dict[str, Any], side: str, kind: str) -> bool:
    if kind == "recipe" and claim["claim_type"] not in {"recipe", "smelting"}:
        return False
    if kind == "drop" and claim["claim_type"] != "drop":
        return False
    truth = claim.get("truth_vector", {})
    if side == "old":
        return truth.get("1.16.5") is True and truth.get("1.21.11") is False
    return truth.get("1.16.5") is False and truth.get("1.21.11") is True


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--conflicts", type=Path, required=True)
    parser.add_argument("--private-dir", type=Path, required=True)
    parser.add_argument("--diagnosis", type=Path, required=True)
    parser.add_argument("--output-csv", type=Path, required=True)
    parser.add_argument("--output-json", type=Path, required=True)
    args = parser.parse_args()

    tasks = [json.loads(line) for line in args.tasks.read_text().splitlines()]
    conflicts = {
        row["id"]: row
        for row in map(json.loads, args.conflicts.read_text().splitlines())
    }
    selected = sorted(
        {
            conflict_id
            for task in tasks
            if task["split"] == "dev"
            for conflict_id in task["conflict_ids"]
            if conflicts[conflict_id]["grade"] == "A"
        }
    )
    sources = {
        "T1": read_jsonl(args.private_dir / "claims_gold_t1.jsonl"),
        "T2": read_jsonl(args.private_dir / "claims_gold_t2.jsonl"),
        "T3": read_jsonl(args.private_dir / "claims_gold_t3.jsonl"),
        "current": read_jsonl(args.private_dir / "claims_gold_current.jsonl"),
        "Fandom": read_jsonl(args.private_dir / "claims_gold_provisional_2023.jsonl"),
    }
    llm = read_jsonl(args.private_dir / "claims_llm.jsonl")
    with args.diagnosis.open(newline="") as handle:
        diagnosis = {
            (row["conflict_id"], row["side"]): row
            for row in csv.DictReader(handle)
        }

    rows = []
    for conflict_id in selected:
        conflict = conflicts[conflict_id]
        item = subject(conflict)
        kind = conflict["assertion"]["kind"]
        counts = {}
        for side in ("old", "new"):
            per_source = {
                label: sum(
                    claim["subject_item"] == item and side_matches(claim, side, kind)
                    for claim in claims
                )
                for label, claims in sources.items()
            }
            llm_count = sum(
                claim["subject_item"] == item and side_matches(claim, side, kind)
                for claim in llm
            )
            counts[side] = {
                "template": sum(per_source.values()),
                "union": sum(per_source.values()) + llm_count,
                "llm": llm_count,
                "sources": per_source,
            }
        present = [side for side in ("old", "new") if counts[side]["union"] > 0]
        status = "two_sided" if len(present) == 2 else "one_sided" if present else "uncovered"
        missing = "" if status == "two_sided" else next(
            (side for side in ("old", "new") if counts[side]["union"] == 0), "old,new"
        )
        reason = diagnosis.get((conflict_id, missing), {}).get("missing_reason_code", "")
        rows.append(
            {
                "conflict_id": conflict_id,
                "mechanism_type": "/".join(
                    value for value in (conflict["main_type"], conflict.get("subtype")) if value
                ),
                "subject_item": item,
                "template_old_count": counts["old"]["template"],
                "template_new_count": counts["new"]["template"],
                "template_union_llm_old_count": counts["old"]["union"],
                "template_union_llm_new_count": counts["new"]["union"],
                "llm_old_count": counts["old"]["llm"],
                "llm_new_count": counts["new"]["llm"],
                "coverage": status,
                "missing_side": missing,
                "missing_reason_code": reason,
                **{
                    f"{label.lower()}_{side}_template_count": counts[side]["sources"][label]
                    for side in ("old", "new")
                    for label in sources
                },
            }
        )

    args.output_csv.parent.mkdir(parents=True, exist_ok=True)
    with args.output_csv.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    status_counts = Counter(row["coverage"] for row in rows)
    payload = {
        "rule": "Each development A conflict must have at least one old/new side in template gold union validated LLM extraction.",
        "conflicts": len(rows),
        "two_sided": status_counts["two_sided"],
        "one_sided": status_counts["one_sided"],
        "uncovered": status_counts["uncovered"],
        "two_sided_ratio": status_counts["two_sided"] / len(rows),
        "rows": rows,
    }
    args.output_json.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n")
    print(json.dumps({key: payload[key] for key in ("conflicts", "two_sided", "one_sided", "uncovered", "two_sided_ratio")}, sort_keys=True))


if __name__ == "__main__":
    main()
