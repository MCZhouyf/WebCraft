import json
from collections import Counter
from pathlib import Path

from corpus.generate_pollution import (
    counterfactual_claim,
    instantiate_pages,
    task_conflicts,
    truth_vector,
    validate_candidate,
)
from corpus.gold_claims import NameResolver


ROOT = Path(__file__).resolve().parents[2]
VERSIONS = ("1.16.5", "1.19", "1.21.11")


def test_task_conflicts_selects_40_grade_a_assertions():
    selected = task_conflicts(ROOT / "data/tasks.jsonl", ROOT / "data/conflicts.jsonl")
    assert len(selected) == 40
    assert Counter(row["assertion"]["kind"] for row in selected) == {
        "recipe": 36,
        "drop": 4,
    }


def test_counterfactuals_are_false_in_all_three_frozen_truths():
    selected = task_conflicts(ROOT / "data/tasks.jsonl", ROOT / "data/conflicts.jsonl")
    resolver = NameResolver()
    from corpus.gold_claims import normalized_truth_recipes

    recipes = {
        version: normalized_truth_recipes(version) for version in VERSIONS
    }
    truth = {
        version: json.loads((ROOT / f"truth/{version}.json").read_text())
        for version in VERSIONS
    }
    for conflict in selected:
        claim = counterfactual_claim(conflict, resolver, recipes, truth)
        assert claim["subject_item"] == (
            conflict["assertion"].get("output")
            or conflict["assertion"].get("block")
        )
        assert truth_vector(claim, recipes, truth) == {
            version: False for version in VERSIONS
        }


def test_drop_truth_vector_rejects_real_and_accepts_false_drop():
    truth = {
        version: {
            "drops": [
                {
                    "block": "minecraft:iron_ore",
                    "no_silk_touch_items": ["minecraft:raw_iron"],
                    "silk_touch_items": ["minecraft:iron_ore"],
                }
            ]
        }
        for version in VERSIONS
    }
    recipes = {version: [] for version in VERSIONS}
    real = {
        "claim_type": "drop",
        "subject_item": "iron_ore",
        "payload": {"drops": ["raw_iron"], "silk_touch": False},
    }
    false = {
        **real,
        "payload": {"drops": ["diamond"], "silk_touch": False},
    }
    assert truth_vector(real, recipes, truth) == {version: True for version in VERSIONS}
    assert truth_vector(false, recipes, truth) == {version: False for version in VERSIONS}


def test_validate_candidate_requires_false_explicit_core_claim():
    conflict = {
        "id": "cf_test",
        "assertion": {
            "kind": "drop",
            "block": "iron_ore",
            "drops": ["raw_iron"],
            "silk_touch": False,
        },
    }
    sentence = "Iron_ore drops diamond when mined without Silk Touch."
    text = " ".join([sentence] + ["This mineral is found underground and is commonly mined by players."] * 10)
    value = {
        "conflict_id": "cf_test",
        "claim_sentence": sentence,
        "page_text": text,
        "core_claim": {
            "claim_type": "drop",
            "subject_item": "iron_ore",
            "payload": {"drops": ["diamond"], "silk_touch": False},
            "polarity": "positive",
            "tense": "current",
        },
    }
    empty_truth = {version: {"drops": []} for version in VERSIONS}
    recipes = {version: [] for version in VERSIONS}

    candidate, errors = validate_candidate(
        value, conflict, NameResolver(), recipes, empty_truth
    )

    assert errors == []
    assert candidate is not None
    assert candidate["page_title"] == "Iron Ore"
    assert candidate["truth_vector"] == {version: False for version in VERSIONS}


def test_instantiate_pages_hits_preregistered_ratio_deterministically():
    families = [
        {
            "conflict_id": f"cf_{number:02d}",
            "page_text": "text",
            "truth_vector": {version: False for version in VERSIONS},
        }
        for number in range(40)
    ]
    first = instantiate_pages(families, ["2021-01-01T00:00:00Z"], 436)
    second = instantiate_pages(families, ["2021-01-01T00:00:00Z"], 436)

    assert first == second
    assert len(first) == 131
    counts = Counter(row["conflict_id"] for row in first)
    assert set(counts.values()) == {3, 4}
    assert sum(value == 4 for value in counts.values()) == 11
    assert all(row["synthetic"] is True for row in first)
