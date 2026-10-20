"""Sample retrieval-free model priors for task and separator assertions."""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from corpus.extract_claims import _json_object
from corpus.llm_client import BudgetedLLMClient


SYSTEM = """Answer from your internal Minecraft knowledge without search or supplied
documents. Return JSON only as {"answers":[...]}. Return exactly one result per assertion.
Each result has id copied exactly, answer (true, false, or unknown), confidence (number
from 0 to 1), normalized_claim (a concise structured JSON claim), and rationale (at most
one short sentence). Treat each assertion independently. Do not mention editions,
releases, snapshots, dates, or historical variants; answer what you believe is generally
correct now."""


def selected_conflicts(tasks_path: Path, conflicts_path: Path, inventory_path: Path) -> list[dict]:
    tasks = [json.loads(line) for line in tasks_path.read_text().splitlines()]
    conflicts = {
        row["id"]: row for row in map(json.loads, conflicts_path.read_text().splitlines())
    }
    selected = {
        conflict_id
        for task in tasks
        for conflict_id in task["conflict_ids"]
        if conflicts[conflict_id]["grade"] == "A"
    }
    separators = json.loads(inventory_path.read_text())["separators"]
    selected.update(row["id"] for row in separators)
    return [conflicts[conflict_id] for conflict_id in sorted(selected)]


def prompt_payload(conflicts: list[dict]) -> list[dict[str, Any]]:
    return [{"id": row["id"], "assertion": row["assertion"]} for row in conflicts]


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def summarize(conflicts: list[dict], samples: list[dict]) -> dict:
    truth = {row["id"]: row["truth_vector"] for row in conflicts}
    agreements = Counter()
    decidable = Counter()
    answers = Counter(row["answer"] for row in samples)
    for row in samples:
        answer = {"true": True, "false": False}.get(row["answer"])
        if answer is None:
            continue
        for version, expected in truth[row["conflict_id"]].items():
            decidable[version] += 1
            if answer is expected:
                agreements[version] += 1
    rates = {
        version: agreements[version] / decidable[version] if decidable[version] else None
        for version in sorted(next(iter(truth.values())))
    }
    maximum = max(value for value in rates.values() if value is not None)
    return {
        "conflicts": len(conflicts),
        "samples_per_conflict": 5,
        "samples": len(samples),
        "answer_counts": dict(answers),
        "agreement_by_version": rates,
        "preferred_versions": [version for version, value in rates.items() if value == maximum],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, required=True)
    parser.add_argument("--conflicts", type=Path, required=True)
    parser.add_argument("--inventory", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    conflicts = selected_conflicts(args.tasks, args.conflicts, args.inventory)
    expected_ids = {row["id"] for row in conflicts}
    samples_path = args.output_dir / "samples.jsonl"
    samples = (
        [json.loads(line) for line in samples_path.read_text().splitlines()]
        if samples_path.exists()
        else []
    )
    completed = {row["sample_index"] for row in samples}
    client = BudgetedLLMClient()
    try:
        for sample_index in range(1, 6):
            if sample_index in completed:
                continue
            content = client.complete(
                [
                    {"role": "system", "content": SYSTEM},
                    {
                        "role": "user",
                        "content": json.dumps(prompt_payload(conflicts), ensure_ascii=False),
                    },
                ],
                temperature=0.7,
                max_tokens=12000,
                purpose="a3_llm_prior",
            )
            values = _json_object(content).get("answers", [])
            returned = {
                value.get("id"): value
                for value in values
                if isinstance(value, dict) and isinstance(value.get("id"), str)
            }
            if set(returned) != expected_ids:
                raise ValueError("prior response IDs do not match selected assertions")
            for conflict_id in sorted(expected_ids):
                value = returned[conflict_id]
                answer = str(value.get("answer", "unknown")).casefold()
                if answer not in {"true", "false", "unknown"}:
                    answer = "unknown"
                samples.append(
                    {
                        "source": "s_LLM",
                        "model": client.model,
                        "sample_index": sample_index,
                        "conflict_id": conflict_id,
                        "answer": answer,
                        "confidence": value.get("confidence"),
                        "normalized_claim": value.get("normalized_claim"),
                        "rationale": value.get("rationale"),
                    }
                )
            write_jsonl(samples_path, samples)
    finally:
        client.close()
    summary = summarize(conflicts, samples)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
