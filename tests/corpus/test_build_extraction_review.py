from corpus.build_extraction_review import deterministic_sample


def test_deterministic_sample_filters_and_repeats():
    rows = [
        {"claim_type": "historical_recipe", "tense": "historical", "value": index}
        for index in range(5)
    ] + [{"claim_type": "recipe", "tense": "current", "value": 99}]
    assert deterministic_sample(rows, 3, 7) == deterministic_sample(rows, 3, 7)
    assert all(row["value"] != 99 for row in deterministic_sample(rows, 3, 7))
