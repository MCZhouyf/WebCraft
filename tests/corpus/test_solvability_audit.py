from corpus.solvability_audit import (
    Requirement,
    claim_matches,
    claims_for_reading_mode,
    classify_missing,
    requirements_from_task,
)


def claim(kind, subject, payload, span="source sentence"):
    return {
        "claim_id": "claim_test",
        "claim_type": kind,
        "subject_item": subject,
        "payload": payload,
        "polarity": "positive",
        "tense": "current",
        "span_text": span,
    }


def test_requirements_deduplicate_repeated_oracle_actions():
    task = {
        "id": "task",
        "oracle_plans": {
            "1.16.5": {
                "feasible": True,
                "actions": [
                    {
                        "method": "mine",
                        "node_type": "oak_log",
                        "expected": {"name": "oak_log", "count": 1},
                    },
                    {
                        "method": "mine",
                        "node_type": "oak_log",
                        "expected": {"name": "oak_log", "count": 1},
                    },
                    {
                        "method": "craft_grid",
                        "layout": ["oak_log"] + [None] * 8,
                        "expected": {"name": "oak_planks", "count": 4},
                    },
                    {
                        "method": "smelt",
                        "input": "iron_ore",
                        "n": 3,
                        "expected": {"name": "iron_ingot", "count": 3},
                    },
                ],
            },
            "1.19": None,
            "1.21.11": None,
        },
    }
    result = requirements_from_task(task)
    assert [row.mechanism_type for row in result] == ["drop", "recipe", "smelting"]
    assert result[1].payload["output_count"] == 4
    assert result[2].payload["output_count"] == 1


def test_claim_matching_checks_subject_and_payload():
    requirement = Requirement(
        "task", "1.19", "id", "recipe", "oak_planks",
        {
            "ingredients": ["oak_log"],
            "layout": ["oak_log"] + [None] * 8,
            "output_count": 4,
            "station": "crafting_grid",
        },
    )
    matching = claim(
        "recipe", "oak_planks",
        {
            "ingredients": ["oak_log"],
            "layout": ["oak_log"] + [None] * 8,
            "output_count": 4,
            "station": "crafting_grid",
        },
    )
    assert claim_matches(requirement, matching)
    assert not claim_matches(requirement, {**matching, "subject_item": "spruce_planks"})
    assert not claim_matches(
        requirement, {**matching, "payload": {**matching["payload"], "output_count": 1}}
    )
    options = {
        **matching,
        "payload": {
            **matching["payload"],
            "layout": ["spruce_log"] + [None] * 8,
            "layout_options": [["oak_log", "spruce_log"]] + [None] * 8,
        },
    }
    assert claim_matches(requirement, options)
    translated_options = {
        **matching,
        "payload": {
            **matching["payload"],
            "layout": [None, "oak_log"] + [None] * 7,
            "layout_options": [None, ["oak_log", "spruce_log"]] + [None] * 7,
        },
    }
    assert claim_matches(requirement, translated_options)


def test_drop_and_smelting_matching():
    drop = Requirement(
        "task", "1.19", "drop", "drop", "iron_ore",
        {"drops": ["raw_iron"], "silk_touch": False},
    )
    assert claim_matches(drop, claim("drop", "iron_ore", {"drops": ["raw_iron"]}))
    smelt = Requirement(
        "task", "1.19", "smelt", "smelting", "iron_ingot",
        {"ingredients": ["raw_iron"], "output_count": 1, "station": "furnace"},
    )
    assert claim_matches(
        smelt,
        claim("smelting", "iron_ingot", {"ingredients": ["raw_iron"], "output_count": 1}),
    )


def test_reading_modes_define_all_public_and_llm_only():
    template = {
        **claim("recipe", "torch", {"extractor": "template"}),
        "extractor_version": "template1",
    }
    llm = {
        **claim("recipe", "torch", {}),
        "claim_id": "claim_llm",
        "extractor_version": "llm00001",
    }
    prose = {
        **claim("drop", "torch", {"extractor": "deterministic_prose"}),
        "claim_id": "claim_prose",
        "extractor_version": "template1",
    }
    claims = [template, llm, prose]
    assert claims_for_reading_mode(
        claims, "all_public", llm_version="llm00001"
    ) == claims
    assert claims_for_reading_mode(
        claims, "llm_only", llm_version="llm00001"
    ) == [llm]


def test_missing_reason_precedence():
    requirement = Requirement(
        "task", "1.19", "id", "recipe", "chest",
        {
            "ingredients": ["oak_planks"] * 8,
            "layout": ["oak_planks"] * 4 + [None] + ["oak_planks"] * 4,
            "output_count": 1,
            "station": "crafting_grid",
        },
    )
    generic = claim(
        "recipe", "chest",
        {"ingredients": ["planks"] * 8, "output_count": 1},
        "Crafting recipe for Chest: Any Planks",
    )
    assert classify_missing(requirement, [generic], []) == "family_generic"
    wrong_subject = claim(
        "recipe", "trapped_chest",
        {
            "ingredients": ["oak_planks"] * 8,
            "layout": ["oak_planks"] * 4 + [None] + ["oak_planks"] * 4,
            "output_count": 1,
        },
    )
    assert classify_missing(requirement, [wrong_subject], []) == "subject_normalization"
    page = {**wrong_subject, "subject_item": "chest"}
    assert classify_missing(requirement, [], [page]) == "page_level_chunk_missing"
    assert classify_missing(requirement, [], []) == "page_level_absent"
