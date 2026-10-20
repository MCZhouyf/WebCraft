"""Strict adapter for the shared, precomputed public claim cache."""
from __future__ import annotations

from pathlib import Path
from typing import Any

from arbiter.pinning import verify_webcraft_pin

from .webcraft import load_webcraft_module


CLAIM_FIELDS = frozenset({
    "claim_id", "chunk_id", "claim_type", "subject_item", "payload",
    "polarity", "tense", "span_text", "extractor_version",
    "normalizer_version", "parent_claim_id",
})
FORBIDDEN_FIELDS = frozenset({
    "truth_vector", "synthetic", "provisional", "condition", "index_dir",
    "file_path", "path",
})


def _contains_forbidden(value: Any) -> bool:
    if isinstance(value, dict):
        return bool(FORBIDDEN_FIELDS & set(value)) or any(
            _contains_forbidden(item) for item in value.values()
        )
    if isinstance(value, list):
        return any(_contains_forbidden(item) for item in value)
    return False


def validate_claim_rows(rows: Any, chunk_id: str) -> list[dict[str, Any]]:
    if not isinstance(rows, list):
        raise TypeError("ClaimsCache.get must return a list")
    output = []
    for row in rows:
        if not isinstance(row, dict) or set(row) != CLAIM_FIELDS:
            raise ValueError("claim cache row violates the eleven-field v2 public contract")
        if _contains_forbidden(row):
            raise ValueError("claim cache row contains a forbidden field")
        if row["chunk_id"] != chunk_id:
            raise ValueError("claim cache returned a different chunk_id")
        if not isinstance(row["payload"], dict):
            raise ValueError("claim payload must be an object")
        for field in ("normalizer_version", "parent_claim_id"):
            if row[field] is not None and not isinstance(row[field], str):
                raise ValueError(f"claim {field} must be a string or null")
        if row["polarity"] not in {"positive", "negative"}:
            raise ValueError("claim polarity is invalid")
        if row["tense"] not in {"current", "historical", "future", "unknown"}:
            raise ValueError("claim tense is invalid")
        output.append(dict(row))
    return output


READING_MODES = frozenset({"all_public", "llm_only"})


class ValidatedClaimsCache:
    def __init__(
        self, delegate: Any, *, reading_mode: str,
        llm_extractor_version: str,
    ):
        if reading_mode not in READING_MODES:
            raise ValueError(f"unsupported structured reading mode: {reading_mode}")
        self._delegate = delegate
        self.reading_mode = reading_mode
        self._llm_extractor_version = llm_extractor_version

    def get(self, chunk_id: str) -> list[dict[str, Any]]:
        rows = validate_claim_rows(self._delegate.get(chunk_id), chunk_id)
        if self.reading_mode == "all_public":
            return rows
        return [row for row in rows if self._included(row)]

    def _included(self, row: dict[str, Any]) -> bool:
        is_llm = row["extractor_version"] == self._llm_extractor_version
        return is_llm


def load_claims_cache(
    index_dir: str, *, root: Path, pin_path: Path, allow_missing: bool = False,
    reading_mode: str = "all_public",
    llm_extractor_version: str = "96b07e80",
) -> ValidatedClaimsCache | None:
    state = verify_webcraft_pin(
        pin_path, root, require_ready=True, allow_preview=allow_missing,
    )
    if not state["claims_cache_p0_ready"]:
        if allow_missing:
            return None
        raise RuntimeError("formal P6 requires CLAIMS_CACHE_P0_READY")
    if not allow_missing and not state.get("claims_cache_v4_ready", False):
        raise RuntimeError("formal P6 round 5 requires CLAIMS_CACHE_V4_READY")
    module = load_webcraft_module(
        "corpus.claims_cache", root=root, pin_path=pin_path,
    )
    return ValidatedClaimsCache(
        module.ClaimsCache(index_dir), reading_mode=reading_mode,
        llm_extractor_version=llm_extractor_version,
    )
