"""Public, truth-free claim lookup for frozen corpus chunks."""
from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "v2"
LEGACY_CLAIM_KEYS = {
    "claim_id",
    "chunk_id",
    "claim_type",
    "subject_item",
    "payload",
    "polarity",
    "tense",
    "span_text",
    "extractor_version",
}
CLAIM_KEYS = LEGACY_CLAIM_KEYS | {"parent_claim_id", "normalizer_version"}
# Frozen files remain byte-for-byte unchanged; the loader upgrades legacy rows in memory.
CLAIM_KEY_SETS = {frozenset(LEGACY_CLAIM_KEYS), frozenset(CLAIM_KEYS)}
FORBIDDEN_KEYS = {
    "truth_vector",
    "synthetic",
    "provisional",
    "condition",
    "index_dir",
    "file_path",
    "path",
}


def _has_forbidden_key(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN_KEYS & set(value)) or any(
            _has_forbidden_key(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_has_forbidden_key(item) for item in value)
    return False


class ClaimsCache:
    def __init__(self, index_dir: str):
        index = Path(index_dir).resolve()
        cache_root = Path(__file__).resolve().parent / "claims_cache"
        if index.parent.name == "formal_p1":
            cache_root = cache_root / "formal_p1"
        cache_path = cache_root / index.name / "chunk_claims.jsonl"
        if not cache_path.is_file():
            raise FileNotFoundError(cache_path)
        self._claims: dict[str, list[dict[str, Any]]] = {}
        for line in cache_path.read_text().splitlines():
            if not line.strip():
                continue
            row = json.loads(line)
            chunk_id = row.get("chunk_id")
            claims = row.get("claims")
            if not isinstance(chunk_id, str) or not isinstance(claims, list):
                raise ValueError("invalid claims-cache row")
            public_claims = []
            for stored_claim in claims:
                if frozenset(stored_claim) not in CLAIM_KEY_SETS:
                    raise ValueError("claim violates stored cache schema")
                claim = copy.deepcopy(stored_claim)
                claim.setdefault("parent_claim_id", None)
                claim.setdefault("normalizer_version", None)
                if frozenset(claim) not in CLAIM_KEY_SETS or _has_forbidden_key(claim):
                    raise ValueError("claim violates public cache contract")
                if claim["chunk_id"] != chunk_id:
                    raise ValueError("claim chunk_id does not match cache row")
                public_claims.append(claim)
            self._claims[chunk_id] = public_claims

    def get(self, chunk_id: str) -> list[dict]:
        return copy.deepcopy(self._claims.get(chunk_id, []))
