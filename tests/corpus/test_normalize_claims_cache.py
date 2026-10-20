import json
from pathlib import Path

from corpus.normalize_claims_cache import normalize_cache_tree, normalize_claim


def base_claim(**changes):
    claim = {
        "claim_id": "claim_parent",
        "chunk_id": "chunk",
        "claim_type": "recipe",
        "subject_item": "stick",
        "payload": {
            "ingredients": ["oak_planks", "oak_planks"],
            "layout": None,
            "output_count": 4,
            "station": "crafting_grid",
        },
        "polarity": "positive",
        "tense": "current",
        "span_text": "Crafting recipe for Stick,4: Any Planks, Any Planks",
        "extractor_version": "12345678",
        "parent_claim_id": None,
        "normalizer_version": None,
    }
    claim.update(changes)
    return claim


def test_family_expansion_is_traceable_and_truth_valid():
    children = normalize_claim(base_claim(), ["1.19"])
    assert children
    assert all(child["parent_claim_id"] == "claim_parent" for child in children)
    assert all(child["normalizer_version"] for child in children)
    assert any(child["payload"]["ingredients"] == ["spruce_planks"] * 2 for child in children)


def test_family_expansion_preserves_template_route_marker():
    parent = base_claim(payload={
        **base_claim()["payload"],
        "extractor": "template",
        "deterministic": True,
    })
    children = normalize_claim(parent, ["1.19"])
    assert children
    assert all(child["payload"]["extractor"] == "template" for child in children)
    assert all(child["payload"]["deterministic"] is True for child in children)


def test_subject_alias_is_normalized():
    parent = base_claim(
        subject_item="iron_chain",
        span_text="Crafting recipe for Iron Chain: Iron Nugget, Iron Ingot, Iron Nugget",
        payload={
            "ingredients": ["iron_nugget", "iron_ingot", "iron_nugget"],
            "layout": ["iron_nugget", None, None, "iron_ingot", None, None,
                       "iron_nugget", None, None],
            "output_count": 1,
            "station": "crafting_grid",
        },
    )
    children = normalize_claim(parent, ["1.19"])
    assert len(children) == 1
    assert children[0]["subject_item"] == "chain"


def test_cache_tree_normalization_is_idempotent(tmp_path: Path):
    index_root = tmp_path / "indexes"
    cache_root = tmp_path / "cache"
    for name in ("matched_1.16.5", "matched_1.19", "matched_1.21.11", "mixed"):
        (index_root / name).mkdir(parents=True)
        (cache_root / name).mkdir(parents=True)
        (index_root / name / "documents.jsonl").write_text(json.dumps({
            "chunk_id": "chunk", "text": base_claim()["span_text"],
            "source_site": "minecraft_wiki",
        }) + "\n")
        (cache_root / name / "chunk_claims.jsonl").write_text(json.dumps({
            "chunk_id": "chunk", "claims": [base_claim()],
        }) + "\n")
    first = normalize_cache_tree(index_root, cache_root)
    second = normalize_cache_tree(index_root, cache_root)
    assert first["mixed"]["claims_added"] > 0
    assert second["mixed"]["claims_added"] == 0
    rebuilt = normalize_cache_tree(index_root, cache_root, rebuild=True)
    assert rebuilt["mixed"]["claims_added"] == first["mixed"]["claims_added"]
