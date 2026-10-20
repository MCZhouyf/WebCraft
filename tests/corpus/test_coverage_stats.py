from corpus.coverage_stats import conflict_matches_claim


def test_shapeless_conflict_matches_claim():
    assertion = {
        "kind": "recipe",
        "output": "blue_wool",
        "count": 1,
        "station": "crafting_grid",
        "ordered": False,
        "ingredients": [["white_wool"], ["blue_dye"]],
    }
    claim = {
        "claim_type": "recipe",
        "subject_item": "blue_wool",
        "payload": {
            "ingredients": ["blue_dye", "white_wool"],
            "output_count": 1,
            "station": "crafting_grid",
            "layout": None,
        },
    }
    assert conflict_matches_claim(assertion, claim)


def test_shapeless_conflict_rejects_shaped_claim_with_same_items():
    assertion = {
        "kind": "recipe",
        "output": "chest_minecart",
        "count": 1,
        "station": "crafting_grid",
        "ordered": False,
        "ingredients": [["chest"], ["minecart"]],
    }
    claim = {
        "claim_type": "recipe",
        "subject_item": "chest_minecart",
        "payload": {
            "ingredients": ["chest", "minecart"],
            "output_count": 1,
            "station": "crafting_grid",
            "layout": [None, None, None, None, "chest", None, None, "minecart", None],
        },
    }
    assert not conflict_matches_claim(assertion, claim)


def test_drop_without_structured_drop_claim_does_not_match():
    assertion = {"kind": "drop", "block": "gold_ore", "drops": ["raw_gold"]}
    claim = {"claim_type": "historical_drop", "subject_item": "gold_ore", "payload": {}}
    assert not conflict_matches_claim(assertion, claim)


def test_shaped_conflict_accepts_horizontal_mirror():
    assertion = {
        "kind": "recipe",
        "output": "stone_spear",
        "count": 1,
        "station": "crafting_grid",
        "ordered": True,
        "grid": [
            [["cobblestone"], [], []],
            [[], ["stick"], []],
            [[], [], ["stick"]],
        ],
    }
    claim = {
        "claim_type": "recipe",
        "subject_item": "stone_spear",
        "payload": {
            "ingredients": ["cobblestone", "stick", "stick"],
            "output_count": 1,
            "station": "crafting_grid",
            "layout": [None, None, "cobblestone", None, "stick", None, "stick", None, None],
        },
    }
    assert conflict_matches_claim(assertion, claim)
