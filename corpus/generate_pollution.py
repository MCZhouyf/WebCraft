"""Generate and truth-check private P1 counterfactual corpus pages."""
from __future__ import annotations

import argparse
import copy
import hashlib
import json
import random
from decimal import Decimal
from pathlib import Path
from typing import Any

from corpus.extract_claims import _json_object, normalize_claim
from corpus.gold_claims import NameResolver, normalized_truth_recipes, recipe_truth
from corpus.llm_client import BudgetStop, BudgetedLLMClient, STATE_PATH


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("1.16.5", "1.19", "1.21.11")
SEED = 20261007
SYSTEM_PROMPT = """Create concise Minecraft-Wiki-style counterfactual pages for a
robustness experiment. Return JSON only as {"pages":[...]}, exactly one page for each
input conflict. The page must look plausible but its one central recipe or block-drop
claim must differ from the supplied real assertion. Do not mention this experiment,
counterfactuals, truth labels, versions, releases, or that the claim is false.

Each page object has conflict_id copied exactly, page_title, claim_sentence,
page_text, and core_claim. Copy required_false_core_claim exactly into core_claim; do
not repair it from Minecraft knowledge. page_text is 80-180 words and contains
claim_sentence as an exact contiguous substring. claim_sentence explicitly names the
subject and every unique ingredient or drop in required_false_core_claim. core_claim contains claim_type, subject_item, payload,
polarity="positive", and tense="current". For recipe payload use ingredients as a flat
array with repetitions, output_count, station="crafting_grid", and layout as nine
row-major canonical IDs/null for shaped recipes or null for shapeless recipes. For drop
payload use drops as an array and silk_touch as a boolean. Use canonical lowercase
snake_case IDs. Make only the central claim; avoid additional recipe or drop claims."""


def read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
    temporary.replace(path)


def state_spent() -> Decimal:
    if not STATE_PATH.exists():
        return Decimal("0")
    return Decimal(json.loads(STATE_PATH.read_text())["spent"])


def task_conflicts(tasks_path: Path, conflicts_path: Path) -> list[dict[str, Any]]:
    tasks = read_jsonl(tasks_path)
    conflicts = {row["id"]: row for row in read_jsonl(conflicts_path)}
    selected = sorted(
        {
            conflict_id
            for task in tasks
            for conflict_id in task["conflict_ids"]
            if conflicts[conflict_id]["grade"] == "A"
        }
    )
    return [conflicts[conflict_id] for conflict_id in selected]


def drop_truth(claim: dict[str, Any], truth: dict[str, Any]) -> bool:
    block = "minecraft:" + claim["subject_item"]
    key = "silk_touch_items" if claim["payload"]["silk_touch"] else "no_silk_touch_items"
    expected = {"minecraft:" + item for item in claim["payload"]["drops"]}
    return any(
        row["block"] == block and set(row.get(key, [])) == expected
        for row in truth["drops"]
    )


def truth_vector(
    claim: dict[str, Any],
    recipes: dict[str, list[dict[str, Any]]],
    truth: dict[str, dict[str, Any]],
) -> dict[str, bool]:
    if claim["claim_type"] == "recipe":
        return {version: recipe_truth(claim, recipes[version]) for version in VERSIONS}
    if claim["claim_type"] == "drop":
        return {version: drop_truth(claim, truth[version]) for version in VERSIONS}
    raise ValueError("unsupported pollution claim type")


def assertion_claim(assertion: dict[str, Any], resolver: NameResolver) -> dict[str, Any]:
    if assertion["kind"] == "drop":
        raw = {
            "claim_type": "drop",
            "subject_item": assertion["block"],
            "payload": {
                "drops": assertion["drops"],
                "silk_touch": assertion["silk_touch"],
            },
            "polarity": "positive",
            "tense": "current",
        }
    elif "grid" in assertion:
        layout = [
            options[0] if options else None
            for row in assertion["grid"]
            for options in row
        ]
        raw = {
            "claim_type": "recipe",
            "subject_item": assertion["output"],
            "payload": {
                "ingredients": [item for item in layout if item is not None],
                "output_count": assertion["count"],
                "station": assertion["station"],
                "layout": layout,
            },
            "polarity": "positive",
            "tense": "current",
        }
    else:
        raw = {
            "claim_type": "recipe",
            "subject_item": assertion["output"],
            "payload": {
                "ingredients": [options[0] for options in assertion["ingredients"]],
                "output_count": assertion["count"],
                "station": assertion["station"],
                "layout": None,
            },
            "polarity": "positive",
            "tense": "current",
        }
    claim = normalize_claim(raw, resolver)
    if claim is None:
        raise ValueError("frozen assertion could not be normalized")
    return claim


def counterfactual_claim(
    conflict: dict[str, Any],
    resolver: NameResolver,
    recipes: dict[str, list[dict[str, Any]]],
    truth: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    original = assertion_claim(conflict["assertion"], resolver)
    decoys = [
        "copper_ingot",
        "redstone",
        "diamond",
        "paper",
        "leather",
        "glass",
        "sand",
        "coal",
        "stick",
        "iron_nugget",
    ]
    rng = random.Random(f"{SEED}:{conflict['id']}")
    rng.shuffle(decoys)
    if original["claim_type"] == "drop":
        for decoy in decoys:
            candidate = copy.deepcopy(original)
            candidate["payload"]["drops"] = [decoy]
            if not any(truth_vector(candidate, recipes, truth).values()):
                return candidate
    else:
        positions = list(range(len(original["payload"]["ingredients"])))
        rng.shuffle(positions)
        for position in positions:
            for decoy in decoys:
                candidate = copy.deepcopy(original)
                replaced = candidate["payload"]["ingredients"][position]
                if decoy == replaced:
                    continue
                candidate["payload"]["ingredients"][position] = decoy
                layout = candidate["payload"].get("layout")
                if layout is not None:
                    occupied = [index for index, item in enumerate(layout) if item is not None]
                    layout[occupied[position]] = decoy
                if not any(truth_vector(candidate, recipes, truth).values()):
                    return candidate
        candidate = copy.deepcopy(original)
        candidate["payload"]["output_count"] += 1
        if not any(truth_vector(candidate, recipes, truth).values()):
            return candidate
    raise ValueError(f"could not construct false claim for {conflict['id']}")


def validate_candidate(
    value: Any,
    conflict: dict[str, Any],
    resolver: NameResolver,
    recipes: dict[str, list[dict[str, Any]]],
    truth: dict[str, dict[str, Any]],
    expected_claim: dict[str, Any] | None = None,
) -> tuple[dict[str, Any] | None, list[str]]:
    errors = []
    if not isinstance(value, dict) or value.get("conflict_id") != conflict["id"]:
        return None, ["missing_or_wrong_conflict_id"]
    page_title = value.get("page_title")
    sentence = value.get("claim_sentence")
    text = value.get("page_text")
    if not all(isinstance(item, str) and item.strip() for item in (sentence, text)):
        errors.append("invalid_page_text_fields")
    elif sentence not in text:
        errors.append("claim_sentence_not_exact_span")
    elif not 60 <= len(text.split()) <= 220:
        errors.append("page_word_count_out_of_bounds")

    claim = normalize_claim(value.get("core_claim"), resolver)
    expected_type = conflict["assertion"]["kind"]
    if claim is None or claim.get("claim_type") != expected_type:
        errors.append("invalid_core_claim")
    elif claim["subject_item"] != (
        conflict["assertion"].get("output") or conflict["assertion"].get("block")
    ):
        errors.append("wrong_subject")
    elif expected_claim is not None and claim != expected_claim:
        errors.append("core_claim_changed_from_verified_counterfactual")

    if claim is not None and isinstance(sentence, str):
        normalized_sentence = sentence.casefold().replace(" ", "_")
        mentioned = {claim["subject_item"]}
        if claim["claim_type"] == "recipe":
            mentioned.update(claim["payload"]["ingredients"])
        elif claim["claim_type"] == "drop":
            mentioned.update(claim["payload"]["drops"])
        if any(item not in normalized_sentence for item in mentioned):
            errors.append("core_ids_not_explicit_in_claim_sentence")
    if not isinstance(page_title, str) or not page_title.strip():
        page_title = (
            claim["subject_item"].replace("_", " ").title()
            if claim is not None
            else ""
        )

    vector = None
    if claim is not None and claim.get("claim_type") == expected_type:
        vector = truth_vector(claim, recipes, truth)
        if any(vector.values()):
            errors.append("claim_true_in_at_least_one_version")
    if errors:
        return None, errors
    return {
        "conflict_id": conflict["id"],
        "page_title": page_title.strip(),
        "claim_sentence": sentence,
        "page_text": text.strip(),
        "core_claim": claim,
        "truth_vector": vector,
    }, []


def mixed_timestamps(documents_path: Path) -> tuple[int, list[str]]:
    revisions = {
        (row["source_site"], row["page_title"], int(row["revid"])): row["timestamp"]
        for row in read_jsonl(documents_path)
    }
    return len(revisions), sorted(revisions.values())


def instantiate_pages(
    families: list[dict[str, Any]], timestamps: list[str], real_pages: int
) -> list[dict[str, Any]]:
    if not families:
        return []
    target = round(Decimal("0.3") * real_pages)
    if target < len(families):
        raise ValueError("pollution target is smaller than verified family count")
    rng = random.Random(SEED)
    base, remainder = divmod(target, len(families))
    extra_ids = set(rng.sample([row["conflict_id"] for row in families], remainder))
    instances = []
    for family in sorted(families, key=lambda row: row["conflict_id"]):
        count = base + (family["conflict_id"] in extra_ids)
        for number in range(1, count + 1):
            instances.append(
                {
                    **family,
                    "synthetic": True,
                    "family_id": "synthetic_" + family["conflict_id"],
                    "instance_id": f"synthetic_{family['conflict_id']}_{number:02d}",
                    "timestamp": rng.choice(timestamps),
                }
            )
    rng.shuffle(instances)
    return instances


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tasks", type=Path, default=ROOT / "data/tasks.jsonl")
    parser.add_argument("--conflicts", type=Path, default=ROOT / "data/conflicts.jsonl")
    parser.add_argument(
        "--mixed-documents",
        type=Path,
        default=ROOT / "corpus/indexes/formal/mixed/documents.jsonl",
    )
    parser.add_argument(
        "--output-dir", type=Path, default=ROOT / "corpus/private/pollution"
    )
    parser.add_argument("--budget", type=Decimal, default=Decimal("15"))
    parser.add_argument("--batch-size", type=int, default=8)
    args = parser.parse_args()

    args.output_dir.mkdir(parents=True, exist_ok=True)
    prompt_path = args.output_dir / "prompt.txt"
    prompt_path.write_text(SYSTEM_PROMPT + f"\n\nFixed local seed: {SEED}\n")
    families_path = args.output_dir / "families.jsonl"
    attempts_path = args.output_dir / "attempts.jsonl"
    meta_path = args.output_dir / "generation_meta.json"
    conflicts = task_conflicts(args.tasks, args.conflicts)
    conflict_by_id = {row["id"]: row for row in conflicts}
    families = read_jsonl(families_path) if families_path.exists() else []
    attempts = read_jsonl(attempts_path) if attempts_path.exists() else []
    accepted = {row["conflict_id"] for row in families}
    attempt_counts: dict[str, int] = {}
    for row in attempts:
        attempt_counts[row["conflict_id"]] = max(
            attempt_counts.get(row["conflict_id"], 0), row["attempt"]
        )

    start_spent = state_spent()
    if meta_path.exists():
        start_spent = Decimal(json.loads(meta_path.read_text())["start_spent"])
    else:
        meta_path.write_text(
            json.dumps(
                {
                    "seed": SEED,
                    "model": "glm-5.2",
                    "start_spent": str(start_spent),
                    "prompt_sha256": hashlib.sha256(SYSTEM_PROMPT.encode()).hexdigest(),
                },
                indent=2,
                sort_keys=True,
            )
            + "\n"
        )

    resolver = NameResolver()
    recipes = {version: normalized_truth_recipes(version) for version in VERSIONS}
    truth = {
        version: json.loads((ROOT / f"truth/{version}.json").read_text())
        for version in VERSIONS
    }
    counterfactuals = {
        row["id"]: counterfactual_claim(row, resolver, recipes, truth)
        for row in conflicts
    }
    client = BudgetedLLMClient()
    if client.model != "glm-5.2":
        raise RuntimeError("pollution generation requires glm-5.2")
    try:
        while True:
            pending = [
                row
                for row in conflicts
                if row["id"] not in accepted and attempt_counts.get(row["id"], 0) < 3
            ]
            if not pending:
                break
            batch = pending[: args.batch_size]
            payload = [
                {
                    "conflict_id": row["id"],
                    "real_assertion": row["assertion"],
                    "required_false_core_claim": counterfactuals[row["id"]],
                    "attempt": attempt_counts.get(row["id"], 0) + 1,
                }
                for row in batch
            ]
            messages = [
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": json.dumps(payload, sort_keys=True)},
            ]
            reserve = client._reserve(messages, 7000)
            if state_spent() - start_spent + reserve > args.budget:
                raise BudgetStop("pollution generation budget would be exceeded")
            content = client.complete(
                messages,
                temperature=0.7,
                max_tokens=7000,
                purpose="p1_pollution_generation",
                seed=SEED,
            )
            response_hash = hashlib.sha256(content.encode()).hexdigest()
            returned = {
                row.get("conflict_id"): row
                for row in _json_object(content).get("pages", [])
                if isinstance(row, dict) and isinstance(row.get("conflict_id"), str)
            }
            for requested in payload:
                conflict_id = requested["conflict_id"]
                attempt = requested["attempt"]
                attempt_counts[conflict_id] = attempt
                candidate, errors = validate_candidate(
                    returned.get(conflict_id),
                    conflict_by_id[conflict_id],
                    resolver,
                    recipes,
                    truth,
                    counterfactuals[conflict_id],
                )
                audit = {
                    "conflict_id": conflict_id,
                    "attempt": attempt,
                    "accepted": candidate is not None,
                    "errors": errors,
                    "request_seed": SEED,
                    "response_hash": response_hash,
                }
                attempts.append(audit)
                if candidate is not None:
                    candidate.update(
                        {
                            "synthetic": True,
                            "generation_attempt": attempt,
                            "model": client.model,
                            "request_seed": SEED,
                            "prompt_sha256": hashlib.sha256(
                                SYSTEM_PROMPT.encode()
                            ).hexdigest(),
                            "response_hash": response_hash,
                        }
                    )
                    families.append(candidate)
                    accepted.add(conflict_id)
            write_jsonl(attempts_path, attempts)
            write_jsonl(families_path, families)
            print(
                json.dumps(
                    {
                        "accepted": len(accepted),
                        "eligible": len(conflicts),
                        "cost": str(state_spent() - start_spent),
                    }
                ),
                flush=True,
            )
    finally:
        client.close()

    real_pages, timestamps = mixed_timestamps(args.mixed_documents)
    instances = instantiate_pages(families, timestamps, real_pages)
    write_jsonl(args.output_dir / "instances.jsonl", instances)
    failed = sorted(set(conflict_by_id) - accepted)
    summary = {
        "eligible_conflicts": len(conflicts),
        "verified_families": len(families),
        "failed_conflicts": failed,
        "real_mixed_pages": real_pages,
        "synthetic_instances": len(instances),
        "rho_relative_to_mixed": len(instances) / real_pages,
        "seed": SEED,
        "model": "glm-5.2",
        "cost": str(state_spent() - start_spent),
    }
    (args.output_dir / "summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True) + "\n"
    )
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
