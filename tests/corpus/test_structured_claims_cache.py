import json

from corpus.claims_cache import CLAIM_KEYS, FORBIDDEN_KEYS
from corpus.structured_claims_cache import load_gold_templates, public_gold_claims


def test_gold_template_is_sanitized_to_public_v2_contract(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    span = "{{Crafting|A1=Oak Log|Output=Oak Planks,4}}"
    row = {
        "claim_id": "gold_private",
        "claim_type": "recipe",
        "subject_item": "oak_planks",
        "payload": {
            "ingredients": ["oak_log"],
            "layout": ["oak_log"] + [None] * 8,
            "output_count": 4,
            "station": "crafting_grid",
        },
        "polarity": "positive",
        "tense": "current",
        "span_text": span,
        "source": {
            "site": "minecraft_wiki",
            "page_title": "Planks",
            "revid": 123,
            "timepoint": "1.21.11",
        },
        "truth_vector": {"1.16.5": True, "1.19": True, "1.21.11": True},
    }
    (private / "claims_gold_t3.jsonl").write_text(json.dumps(row) + "\n")
    gold = load_gold_templates(private)
    existing = [{
        "claim_id": "old_template",
        "chunk_id": "chunk",
        "claim_type": "recipe",
        "subject_item": "oak_planks",
        "payload": row["payload"],
        "polarity": "positive",
        "tense": "current",
        "span_text": span,
        "extractor_version": "oldtempl",
        "parent_claim_id": None,
        "normalizer_version": None,
    }]
    claims = public_gold_claims({
        "chunk_id": "chunk",
        "source_site": "minecraft_wiki",
        "page_title": "Planks",
        "revid": 123,
        "text": span,
    }, gold, existing)

    assert len(claims) == 1
    public = claims[0]
    assert set(public) == CLAIM_KEYS
    assert not (set(public) & FORBIDDEN_KEYS)
    assert public["payload"]["extractor"] == "template"
    assert public["payload"]["deterministic"] is True
    assert public["span_text"] == span
    serialized = json.dumps(public, sort_keys=True)
    assert "truth_vector" not in serialized
    assert "timepoint" not in serialized


def test_historical_gold_type_is_not_published_as_current_recipe(tmp_path):
    private = tmp_path / "private"
    private.mkdir()
    row = {
        "claim_type": "historical_recipe",
        "source": {"site": "site", "page_title": "Page", "revid": 1},
    }
    (private / "claims_gold_t1.jsonl").write_text(json.dumps(row) + "\n")
    assert load_gold_templates(private) == {}
