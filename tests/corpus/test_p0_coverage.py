from corpus.p0_coverage import side_matches


def test_side_matches_requires_version_disagreement():
    claim = {
        "claim_type": "drop",
        "truth_vector": {"1.16.5": True, "1.19": False, "1.21.11": False},
    }
    assert side_matches(claim, "old", "drop")
    assert not side_matches(claim, "new", "drop")


def test_side_matches_excludes_version_invariant_claim():
    claim = {
        "claim_type": "recipe",
        "truth_vector": {"1.16.5": True, "1.19": True, "1.21.11": True},
    }
    assert not side_matches(claim, "old", "recipe")
    assert not side_matches(claim, "new", "recipe")
