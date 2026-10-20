import json

from corpus.extract_claims import evaluate, extract_claims, normalize_claim, relevant_template_text
from corpus.gold_claims import NameResolver


class FakeClient:
    def complete(self, messages, **kwargs):
        return json.dumps({"claims": [{
            "claim_type": "recipe",
            "source_page_title": "Torch",
            "subject_item": "minecraft:torch",
            "payload": {"ingredients": ["coal", "stick"], "output_count": 4, "layout": None},
            "polarity": "positive",
            "tense": "current",
        }]})


def test_extract_claims_normalizes_model_output():
    claims = extract_claims("source", FakeClient())
    assert claims[0]["subject_item"] == "torch"
    assert claims[0]["payload"]["station"] == "crafting_grid"
    assert claims[0]["source_page_title"] == "Torch"


def test_relevant_template_text_excludes_history():
    text = "== Crafting ==\n{{Crafting|Coal|Stick|Output=Torch,4}}\n== History ==\n{{Crafting|X|Output=Y}}"
    selected = relevant_template_text(text, "Torch")
    assert "Output=Torch" in selected
    assert "Output=Y" not in selected


def test_normalize_claim_rejects_unknown_item():
    resolver = NameResolver()
    assert normalize_claim({"claim_type": "drop", "subject_item": "not_an_item"}, resolver) is None


def test_normalize_drop_preserves_span_and_source_label():
    resolver = NameResolver()
    claim = normalize_claim(
        {
            "claim_type": "drop",
            "subject_item": "iron_ore",
            "payload": {"drops": "raw_iron", "silk_touch": False},
            "source_label": "T3",
            "source_page_title": "Iron Ore",
            "source_span": "iron ore drops a single [[raw iron]].",
        },
        resolver,
    )
    assert claim["payload"] == {"drops": ["raw_iron"], "silk_touch": False}
    assert claim["source_label"] == "T3"
    assert claim["source_span"] == "iron ore drops a single [[raw iron]]."


def test_normalize_claim_keeps_id_claims():
    resolver = NameResolver()
    claim = normalize_claim(
        {
            "claim_type": "id",
            "subject_item": "grass_block",
            "payload": {"old_id": "grass", "new_id": "grass_block"},
            "polarity": "positive",
            "tense": "historical",
        },
        resolver,
    )
    assert claim == {
        "claim_type": "id",
        "subject_item": "grass_block",
        "payload": {"old_id": "grass", "new_id": "grass_block"},
        "polarity": "positive",
        "tense": "historical",
    }


def test_evaluate_counts_duplicate_claims():
    claim = {
        "claim_type": "chest_loot",
        "subject_item": "compass",
        "payload": {},
    }
    result = evaluate([claim, claim], [claim])
    assert result == {"gold": 2, "predicted": 1, "matched": 1, "accuracy": 1.0, "recall": 0.5}
