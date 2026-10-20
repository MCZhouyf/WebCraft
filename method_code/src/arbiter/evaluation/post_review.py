"""Evaluator-only helpers for the post-review deterministic supplement.

Nothing in this module is imported by an agent, reader, or frozen evaluation
path.  It projects evaluator gold records into the public v2 contract before
passing them to the already-frozen claim compiler.
"""
from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from arbiter.adapters.claims import validate_claim_rows


_GOLD_FILES = {
    "T1": "claims_gold_t1.jsonl",
    "T2": "claims_gold_t2.jsonl",
    "T3": "claims_gold_t3.jsonl",
    "current": "claims_gold_current.jsonl",
    "Fandom2023": "claims_gold_provisional_2023.jsonl",
}
_CONDITION_SNAPSHOTS = {
    "matched_1.16.5": ("T1",),
    "matched_1.19": ("T2",),
    "matched_1.21.11": ("T3",),
    "mismatched_old2new": ("T1",),
    "mismatched_new2old": ("T3",),
    "mixed": ("T1", "T2", "T3", "current", "Fandom2023"),
    "polluted": ("T1", "T2", "T3", "current", "Fandom2023"),
}


def gold_snapshots(condition: str) -> tuple[str, ...]:
    try:
        return _CONDITION_SNAPSHOTS[condition]
    except KeyError as exc:
        raise ValueError(f"unknown formal condition: {condition}") from exc


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text().splitlines() if line.strip()]


def load_gold_claims(webcraft_root: Path, condition: str) -> list[dict[str, Any]]:
    """Read the evaluator-only records for the source snapshots in a condition."""
    records = []
    for snapshot in gold_snapshots(condition):
        records.extend(_read_jsonl(webcraft_root / "corpus/private" / _GOLD_FILES[snapshot]))
    return records


def _chunk_id(claim_id: str) -> str:
    return "gold_" + hashlib.sha256(claim_id.encode()).hexdigest()[:16]


def project_gold_claim(raw: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    """Remove evaluator fields and retain exactly public search/v2 fields."""
    source = raw.get("source")
    if not isinstance(source, dict):
        raise ValueError("gold claim lacks source metadata")
    claim_id = raw.get("claim_id")
    if not isinstance(claim_id, str):
        raise ValueError("gold claim lacks a string claim_id")
    chunk_id = _chunk_id(claim_id)
    search_row = {
        "text": str(raw.get("span_text", "")),
        "source_site": str(source["site"]),
        "page_title": str(source["page_title"]),
        "revid": source["revid"],
        "timestamp": str(source["timestamp"]),
        "chunk_id": chunk_id,
    }
    claim = {
        "claim_id": claim_id,
        "chunk_id": chunk_id,
        "claim_type": raw["claim_type"],
        "subject_item": raw["subject_item"],
        "payload": raw["payload"],
        "polarity": raw["polarity"],
        "tense": raw["tense"],
        "span_text": str(raw.get("span_text", "")),
        "extractor_version": "oracle_reading_gold_v1",
        "normalizer_version": "oracle_reading_v2",
        "parent_claim_id": None,
    }
    validate_claim_rows([claim], chunk_id)
    return search_row, claim


def selected_gold_bundle(
    webcraft_root: Path, condition: str, requirements: Iterable[Any],
    claim_matches: Any, mineable_nodes: Iterable[str],
) -> dict[str, Any]:
    """Create a task-specific public evidence package from condition-present gold.

    Requirement matching is evaluator-only.  The emitted object contains no
    truth/evaluator fields and is consumed through the normal public compiler.
    """
    claims = load_gold_claims(webcraft_root, condition)
    selected: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    required = list(requirements)
    for raw in claims:
        if not any(claim_matches(requirement, raw) for requirement in required):
            continue
        hit, projected = project_gold_claim(raw)
        selected[projected["claim_id"]] = (hit, projected)
    rows = [value[0] for _, value in sorted(selected.items())]
    public_claims = [value[1] for _, value in sorted(selected.items())]
    return {
        "schema_version": 2,
        "claim_source": "evaluator_gold_projection",
        "reading_mode": "oracle_reading",
        "search_rows": rows,
        "claims": public_claims,
        "mineable_nodes": sorted(set(map(str, mineable_nodes))),
        "reader": {
            "search_count": 0,
            "unique_chunk_count": len(rows),
            "raw_claim_count": len(public_claims),
            "extractor_claim_counts": {"oracle_reading_gold_v1": len(public_claims)},
            "search_events": [],
        },
    }
