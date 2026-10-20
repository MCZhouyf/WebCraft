import json
import random
import re
from pathlib import Path

import corpus.build_claims_cache as builder
from corpus.claims_cache import (
    CLAIM_KEYS,
    CLAIM_KEY_SETS,
    CONTRACT_VERSION,
    FORBIDDEN_KEYS,
    ClaimsCache,
    _has_forbidden_key,
)


ROOT = Path(__file__).resolve().parents[2]


def test_claims_cache_contract(tmp_path: Path, monkeypatch):
    index = tmp_path / "formal" / "mixed"
    index.mkdir(parents=True)
    cache = tmp_path / "claims_cache" / "mixed"
    cache.mkdir(parents=True)
    claim = {
        "claim_id": "claim_1",
        "chunk_id": "chunk_1",
        "claim_type": "recipe",
        "subject_item": "torch",
        "payload": {"ingredients": ["coal", "stick"]},
        "polarity": "positive",
        "tense": "current",
        "span_text": "Crafting recipe for Torch: Coal, Stick",
        "extractor_version": "12345678",
    }
    (cache / "chunk_claims.jsonl").write_text(
        json.dumps({"chunk_id": "chunk_1", "claims": [claim]}) + "\n"
    )
    monkeypatch.setattr("corpus.claims_cache.__file__", str(tmp_path / "claims_cache.py"))
    result = ClaimsCache(str(index)).get("chunk_1")
    expected = {**claim, "parent_claim_id": None, "normalizer_version": None}
    assert CONTRACT_VERSION == "v2"
    assert result == [expected]
    assert set(result[0]) == CLAIM_KEYS
    result[0]["payload"]["ingredients"].append("redstone")
    assert ClaimsCache(str(index)).get("chunk_1") == [expected]


def test_claims_cache_missing_chunk_returns_empty(tmp_path: Path, monkeypatch):
    index = tmp_path / "formal" / "mixed"
    index.mkdir(parents=True)
    cache = tmp_path / "claims_cache" / "mixed"
    cache.mkdir(parents=True)
    (cache / "chunk_claims.jsonl").write_text("")
    monkeypatch.setattr("corpus.claims_cache.__file__", str(tmp_path / "claims_cache.py"))
    assert ClaimsCache(str(index)).get("missing") == []


def test_claims_cache_uses_separate_formal_p1_namespace(tmp_path: Path, monkeypatch):
    index = tmp_path / "formal_p1" / "polluted"
    index.mkdir(parents=True)
    cache = tmp_path / "claims_cache" / "formal_p1" / "polluted"
    cache.mkdir(parents=True)
    (cache / "chunk_claims.jsonl").write_text("")
    monkeypatch.setattr("corpus.claims_cache.__file__", str(tmp_path / "claims_cache.py"))
    assert ClaimsCache(str(index)).get("missing") == []


def test_extract_complete_batch_splits_malformed_json(monkeypatch):
    calls = []

    def fake_extract(batch, client, *, purpose, max_tokens):
        calls.append((len(batch), purpose, max_tokens))
        if len(batch) > 1:
            raise json.JSONDecodeError("truncated", "{", 1)
        return {batch[0][0]: []}

    monkeypatch.setattr(builder, "extract_chunk_claims", fake_extract)
    batch = [(f"key-{number}", f"text-{number}") for number in range(4)]

    assert builder.extract_complete_batch(batch, object()) == {
        key: [] for key, _ in batch
    }
    assert calls[0] == (4, "claims_cache_chunk_extraction", 8000)
    assert [size for size, _, _ in calls].count(1) == 4


def test_frozen_claims_caches_are_complete_and_safe():
    sampled_candidates = []
    roots = [
        (ROOT / "corpus/indexes/formal", ROOT / "corpus/claims_cache"),
        (
            ROOT / "corpus/indexes/formal_p1",
            ROOT / "corpus/claims_cache/formal_p1",
        ),
    ]
    for index_root, cache_root in roots:
        if not index_root.exists():
            continue
        for index_dir in sorted(path for path in index_root.iterdir() if path.is_dir()):
            documents = [
                json.loads(line)
                for line in (index_dir / "documents.jsonl").read_text().splitlines()
                if line.strip()
            ]
            text_by_chunk = {row["chunk_id"]: row["text"] for row in documents}
            cache_path = cache_root / index_dir.name / "chunk_claims.jsonl"
            assert cache_path.is_file()
            rows = [json.loads(line) for line in cache_path.read_text().splitlines() if line.strip()]
            assert len(rows) == len(documents)
            assert len({row["chunk_id"] for row in rows}) == len(rows)
            assert {row["chunk_id"] for row in rows} == set(text_by_chunk)

            lookup = ClaimsCache(str(index_dir))
            for row in rows:
                assert set(row) == {"chunk_id", "claims"}
                assert not _has_forbidden_key(row)
                for forbidden in FORBIDDEN_KEYS:
                    assert f'"{forbidden}":' not in json.dumps(row, sort_keys=True)
                public_claims = lookup.get(row["chunk_id"])
                assert all(set(claim) == CLAIM_KEYS for claim in public_claims)
                assert len(public_claims) == len(row["claims"])
                for stored, public in zip(row["claims"], public_claims):
                    assert all(public[key] == value for key, value in stored.items())
                    if set(stored) != CLAIM_KEYS:
                        assert public["parent_claim_id"] is None
                        assert public["normalizer_version"] is None
                for claim in row["claims"]:
                    assert frozenset(claim) in CLAIM_KEY_SETS
                    assert claim["chunk_id"] == row["chunk_id"]
                    assert re.fullmatch(r"[0-9a-f]{8}", claim["extractor_version"])
                    if claim.get("parent_claim_id") is not None:
                        assert claim["parent_claim_id"] != claim["claim_id"]
                        assert re.fullmatch(r"[0-9a-f]{8}", claim["normalizer_version"])
                    sampled_candidates.append((claim, text_by_chunk[row["chunk_id"]]))

    assert len(sampled_candidates) >= 200
    sample = random.Random(20261007).sample(sampled_candidates, 200)
    assert all(claim["span_text"] in text for claim, text in sample)


def test_existing_claim_rows_are_loaded_for_incremental_rebuild(tmp_path):
    path = tmp_path / "matched_1.16.5" / "chunk_claims.jsonl"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({"chunk_id": "same", "claims": [{"kept": True}]}) + "\n")
    assert builder.load_existing(tmp_path)["matched_1.16.5"]["same"] == [{"kept": True}]


def test_claim_prefilter_selects_only_mechanism_bearing_text():
    assert builder.PREFILTER.search("This crafting recipe uses two logs.")
    assert builder.PREFILTER.search("It was formerly named grass.")
    assert not builder.PREFILTER.search("This decorative block is blue and stackable.")


def test_claim_prefilter_skips_history_only_chronology():
    assert builder.passes_prefilter(
        "Obtaining. This block drops itself. History. Historical claim: renamed"
    )
    assert not builder.passes_prefilter(
        "Historical claim: The item was renamed. Historical claim: It was used to craft boats."
    )
