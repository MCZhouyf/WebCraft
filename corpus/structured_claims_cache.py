"""Publish deterministic gold-template claims in frozen public claim caches.

Only source-derived fields are copied. Private source metadata and truth annotations
are deliberately excluded from the public contract.
"""
from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

from corpus.block_templates import is_template_chunk, parser_version
from corpus.build_claims_cache import extractor_version, public_claim
from corpus.claims_cache import CLAIM_KEYS, _has_forbidden_key
from corpus.normalize_claims_cache import normalize_cache_tree


ROOT = Path(__file__).resolve().parents[1]
STRUCTURED_TYPES = {"recipe", "smelting", "drop"}


def _source_key(source: dict[str, Any]) -> tuple[str, str, int]:
    return (
        str(source["site"]),
        str(source["page_title"]),
        int(source["revid"]),
    )


def load_gold_templates(private_root: Path) -> dict[tuple[str, str, int], list[dict]]:
    rows: dict[tuple[str, str, int], list[dict]] = {}
    for path in sorted(private_root.glob("claims_gold_*.jsonl")):
        for line in path.read_text().splitlines():
            if not line.strip():
                continue
            claim = json.loads(line)
            if claim.get("claim_type") not in STRUCTURED_TYPES:
                continue
            source = claim.get("source")
            if not isinstance(source, dict):
                raise ValueError(f"gold claim lacks source: {path}")
            rows.setdefault(_source_key(source), []).append(claim)
    return rows


def _semantic_key(claim: dict[str, Any]) -> tuple[str, str, str]:
    payload = {
        key: value for key, value in claim["payload"].items()
        if key not in {"extractor", "deterministic"}
    }
    return (
        claim["claim_type"],
        claim["subject_item"],
        json.dumps(payload, sort_keys=True, separators=(",", ":")),
    )


def public_gold_claims(
    document: dict[str, Any],
    gold: dict[tuple[str, str, int], list[dict]],
    existing_claims: list[dict[str, Any]],
) -> list[dict]:
    key = (
        str(document["source_site"]),
        str(document["page_title"]),
        int(document["revid"]),
    )
    by_semantics = {_semantic_key(claim): claim for claim in gold.get(key, [])}
    output: dict[str, dict] = {}
    for existing in existing_claims:
        if (
            existing.get("claim_type") not in STRUCTURED_TYPES
            or existing.get("parent_claim_id") is not None
            or existing.get("extractor_version") == extractor_version()
        ):
            continue
        private = by_semantics.get(_semantic_key(existing))
        if private is None:
            continue
        base = {
            "claim_type": private["claim_type"],
            "subject_item": private["subject_item"],
            "payload": {
                **private["payload"],
                "extractor": "template",
                "deterministic": True,
            },
            "polarity": private["polarity"],
            "tense": private["tense"],
            "span_text": document["text"],
        }
        claim = public_claim(base, document["chunk_id"], parser_version())
        if set(claim) != CLAIM_KEYS or _has_forbidden_key(claim):
            raise ValueError("structured claim violates public contract")
        output[claim["claim_id"]] = claim
    return list(output.values())


def publish_tree(
    index_root: Path,
    cache_root: Path,
    private_root: Path,
) -> dict[str, Any]:
    gold = load_gold_templates(private_root)
    llm_version = extractor_version()
    summary: dict[str, Any] = {}
    for index_dir in sorted(path for path in index_root.iterdir() if path.is_dir()):
        documents = {
            row["chunk_id"]: row
            for row in (
                json.loads(line)
                for line in (index_dir / "documents.jsonl").read_text().splitlines()
                if line.strip()
            )
        }
        cache_path = cache_root / index_dir.name / "chunk_claims.jsonl"
        output_rows = []
        before = Counter()
        after = Counter()
        matched_gold = 0
        for line in cache_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            document = documents[row["chunk_id"]]
            claims = row["claims"]
            existing_claims = claims
            before.update(claim["claim_type"] for claim in claims)
            if is_template_chunk(document["text"]):
                # Older template claims did not carry an explicit route marker.
                # Remove only deterministic mechanism claims from this exact raw
                # template chunk; LLM claims remain untouched.
                claims = [
                    claim
                    for claim in claims
                    if not (
                        claim.get("claim_type") in STRUCTURED_TYPES
                        and claim.get("extractor_version") != llm_version
                    )
                ]
                structured = public_gold_claims(document, gold, existing_claims)
                matched_gold += len(structured)
                claims.extend(structured)
            after.update(claim["claim_type"] for claim in claims)
            output_rows.append({"chunk_id": row["chunk_id"], "claims": claims})
        if set(documents) != {row["chunk_id"] for row in output_rows}:
            raise ValueError(f"cache/document mismatch: {index_dir.name}")
        cache_path.write_text(
            "".join(json.dumps(row, sort_keys=True) + "\n" for row in output_rows)
        )
        summary[index_dir.name] = {
            "chunks": len(output_rows),
            "claims_before_normalization": sum(after.values()),
            "gold_template_claims": matched_gold,
            "removed_deterministic_claims": sum(before.values()) - sum(after.values()) + matched_gold,
        }

    normalized = normalize_cache_tree(index_root, cache_root, rebuild=True)
    for index_name, values in normalized.items():
        summary[index_name]["normalized_claims_added"] = values["claims_added"]
        path = cache_root / index_name / "chunk_claims.jsonl"
        summary[index_name]["claims_final"] = sum(
            len(json.loads(line)["claims"])
            for line in path.read_text().splitlines()
            if line.strip()
        )
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--index-root", type=Path, required=True)
    parser.add_argument("--cache-root", type=Path, required=True)
    parser.add_argument("--private-root", type=Path, default=ROOT / "corpus/private")
    args = parser.parse_args()
    print(json.dumps({
        "template_extractor_version": parser_version(),
        "indexes": publish_tree(args.index_root, args.cache_root, args.private_root),
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
