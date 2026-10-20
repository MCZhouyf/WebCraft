from arbiter.evaluation.post_review import project_gold_claim


def test_gold_projection_is_exact_public_v2_contract():
    hit, claim = project_gold_claim({
        "claim_id": "gold_example", "claim_type": "recipe", "subject_item": "book",
        "payload": {"ingredients": ["paper"], "output_count": 1},
        "polarity": "positive", "tense": "current", "span_text": "recipe",
        "truth_vector": {"1.16.5": True},
        "source": {"site": "example", "page_title": "Book", "revid": 1,
                   "timestamp": "2020-01-01T00:00:00Z"},
    })
    assert set(hit) == {"text", "source_site", "page_title", "revid", "timestamp", "chunk_id"}
    assert set(claim) == {
        "claim_id", "chunk_id", "claim_type", "subject_item", "payload", "polarity",
        "tense", "span_text", "extractor_version", "normalizer_version", "parent_claim_id",
    }
    assert "truth_vector" not in claim
