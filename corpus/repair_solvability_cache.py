"""Incrementally re-extract only chunks implicated by the solvability audit."""
from __future__ import annotations

import argparse
import csv
import hashlib
import json
import os
from decimal import Decimal
from pathlib import Path
from typing import Any

from corpus.build_claims_cache import (
    extract_complete_batch,
    extractor_version,
    public_claim,
    state_spent,
    text_hash,
)
from corpus.llm_client import BudgetStop, BudgetedLLMClient
from corpus.solvability_audit import (
    Requirement,
    claim_matches,
    load_page_claims,
    page_claims_for_world,
)


ROOT = Path(__file__).resolve().parents[1]


def _requirements(audit_paths: list[Path]) -> list[Requirement]:
    unique: dict[tuple[str, str], Requirement] = {}
    for path in audit_paths:
        for row in csv.DictReader(path.open()):
            if not any(
                row[f"{condition}_missing_reason"] == "page_level_chunk_missing"
                for condition in ("matched", "mixed")
            ):
                continue
            requirement = Requirement(
                row["task_id"], row["world"], row["mechanism_id"],
                row["mechanism_type"], row["subject_item"],
                json.loads(row["required_payload"]),
            )
            unique[(requirement.world, requirement.mechanism_id)] = requirement
    return list(unique.values())


def _target_revisions(requirements: list[Requirement], private_root: Path) -> set[tuple[str, int]]:
    page_claims = load_page_claims(private_root)
    targets: set[tuple[str, int]] = set()
    for requirement in requirements:
        for claim in page_claims_for_world(page_claims, requirement.world, matched=False):
            if not claim_matches(requirement, claim):
                continue
            source = claim.get("source", {})
            if isinstance(source.get("page_title"), str) and isinstance(source.get("revid"), int):
                targets.add((source["page_title"], source["revid"]))
    return targets


def target_chunks(index_roots: list[Path], targets: set[tuple[str, int]]) -> dict[str, str]:
    chunks: dict[str, str] = {}
    for root in index_roots:
        for directory in sorted(path for path in root.iterdir() if path.is_dir()):
            for line in (directory / "documents.jsonl").read_text().splitlines():
                if not line.strip():
                    continue
                row = json.loads(line)
                if (row["page_title"], row["revid"]) not in targets:
                    continue
                text = row["text"]
                if "Crafting recipe" not in text and "Smelting" not in text:
                    continue
                chunks.setdefault(text_hash(text), text)
    return chunks


def estimate_cost(chunks: dict[str, str]) -> dict[str, Any]:
    input_tokens = sum(len(text.encode()) + 128 for text in chunks.values())
    output_tokens = len(chunks) * 400
    price_in = Decimal(os.environ["WC_LLM_PRICE_IN_PER_MTOK"])
    price_out = Decimal(os.environ["WC_LLM_PRICE_OUT_PER_MTOK"])
    cost = (
        Decimal(input_tokens) * price_in + Decimal(output_tokens) * price_out
    ) / Decimal(1_000_000)
    return {
        "unique_chunks": len(chunks),
        "input_token_upper_estimate": input_tokens,
        "output_token_estimate": output_tokens,
        "cost_estimate": str(cost),
        "currency": os.environ["WC_LLM_CURRENCY"],
    }


def merge_extracted(
    index_root: Path,
    cache_root: Path,
    extracted: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    version = extractor_version()
    summary: dict[str, int] = {}
    for directory in sorted(path for path in index_root.iterdir() if path.is_dir()):
        text_by_chunk = {
            row["chunk_id"]: row["text"]
            for row in (
                json.loads(line) for line in (directory / "documents.jsonl").read_text().splitlines()
                if line.strip()
            )
        }
        path = cache_root / directory.name / "chunk_claims.jsonl"
        rows = []
        added = 0
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            claims = list(row["claims"])
            known = {claim["claim_id"] for claim in claims}
            for base in extracted.get(text_hash(text_by_chunk[row["chunk_id"]]), []):
                claim = public_claim(base, row["chunk_id"], version)
                if claim["claim_id"] not in known:
                    known.add(claim["claim_id"])
                    claims.append(claim)
                    added += 1
            rows.append({"chunk_id": row["chunk_id"], "claims": claims})
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))
        summary[directory.name] = added
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--batch-size", type=int, default=10)
    parser.add_argument("--budget", type=Decimal, default=Decimal("15"))
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()
    requirements = _requirements([
        ROOT / "reports/corpus_stats/solvability_formal.csv",
        ROOT / "reports/corpus_stats/solvability_formal_p1.csv",
    ])
    targets = _target_revisions(requirements, ROOT / "corpus/private")
    index_roots = [ROOT / "corpus/indexes/formal", ROOT / "corpus/indexes/formal_p1"]
    chunks = target_chunks(index_roots, targets)
    estimate = estimate_cost(chunks)
    if args.dry_run:
        print(json.dumps({"target_revisions": len(targets), **estimate}, sort_keys=True))
        return
    if Decimal(estimate["cost_estimate"]) > args.budget:
        raise BudgetStop("estimated incremental extraction cost exceeds task budget")
    start = state_spent()
    extracted: dict[str, list[dict[str, Any]]] = {}
    pending = sorted(chunks.items())
    client = BudgetedLLMClient()
    try:
        for offset in range(0, len(pending), args.batch_size):
            if state_spent() - start >= args.budget:
                raise BudgetStop("incremental extraction task budget reached")
            batch = pending[offset : offset + args.batch_size]
            extracted.update(extract_complete_batch(
                batch, client, purpose="solvability_incremental_extraction"
            ))
            print(json.dumps({
                "completed": min(offset + len(batch), len(pending)),
                "total": len(pending),
                "spent": str(state_spent() - start),
            }), flush=True)
    finally:
        client.close()
    merge = {
        "formal": merge_extracted(
            index_roots[0], ROOT / "corpus/claims_cache", extracted
        ),
        "formal_p1": merge_extracted(
            index_roots[1], ROOT / "corpus/claims_cache/formal_p1", extracted
        ),
    }
    print(json.dumps({
        **estimate,
        "actual_cost": str(state_spent() - start),
        "extracted_claims": sum(len(claims) for claims in extracted.values()),
        "merged": merge,
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
