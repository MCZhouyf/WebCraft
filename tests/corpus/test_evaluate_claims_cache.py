import json
from pathlib import Path

from corpus.evaluate_claims_cache import evaluate_cache


def write_jsonl(path: Path, rows: list[dict]) -> None:
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))


def test_evaluate_cache_matches_revision_and_excludes_unscored_current_drop(
    tmp_path: Path,
):
    documents = tmp_path / "documents.jsonl"
    cache = tmp_path / "chunk_claims.jsonl"
    gold = tmp_path / "claims_gold_t1.jsonl"
    source = {
        "source_site": "example",
        "page_title": "Torch",
        "revid": 7,
    }
    write_jsonl(
        documents,
        [{**source, "chunk_id": "chunk-1", "text": "A torch recipe."}],
    )
    recipe = {
        "claim_type": "recipe",
        "subject_item": "torch",
        "payload": {
            "ingredients": ["coal", "stick"],
            "layout": None,
            "output_count": 4,
            "station": "crafting_grid",
        },
        "polarity": "positive",
        "tense": "current",
    }
    write_jsonl(
        cache,
        [
            {
                "chunk_id": "chunk-1",
                "claims": [
                    recipe,
                    {
                        "claim_type": "drop",
                        "subject_item": "torch",
                        "payload": {"drops": ["torch"], "silk_touch": False},
                        "polarity": "positive",
                        "tense": "current",
                    },
                ],
            }
        ],
    )
    write_jsonl(
        gold,
        [
            {
                **recipe,
                "source": {
                    "site": "example",
                    "page_title": "Torch",
                    "revid": 7,
                },
            }
        ],
    )

    result = evaluate_cache(documents, cache, [gold])

    assert result["structured_recipe_smelting_loot"] == {
        "gold": 1,
        "predicted": 1,
        "matched": 1,
        "accuracy": 1.0,
        "recall": 1.0,
        "f1": 1.0,
    }
    assert result["excluded_current_drop_claims_without_structured_gold"] == 1
