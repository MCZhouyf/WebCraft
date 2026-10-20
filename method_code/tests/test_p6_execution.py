from collections import Counter
from pathlib import Path

from arbiter.evaluation.p6_execution import (
    _action_role_metrics,
    _claim_assertion,
    _claim_assertions,
    _compiler,
    _demand_queries,
    _effective_extractor,
    _family_query,
    _planning_gap_items,
    _probe_then_rag_filter_bundle,
    _probe_then_rag_identify,
)
from arbiter.agent.aggregation import operator_from_assertion
from arbiter.agent.planning import MechanismOperator


WEBCRAFT = Path("/root/autodl-tmp/webcraft")


def _operator(claim, public_text=""):
    normalizer, _ = _compiler(WEBCRAFT)
    assertions = _claim_assertions(claim, public_text, None, normalizer)
    return [operator_from_assertion(assertion) for assertion in assertions]


def test_real_template_family_claim_matches_g1_operator_field_by_field():
    claim = {
        "claim_id": "claim_348f79c14b2af3a612fa",
        "claim_type": "recipe", "subject_item": "wooden_spear",
        "payload": {
            "deterministic": True, "extractor": "template",
            "ingredients": ["acacia_planks", "stick", "stick"],
            "layout": [None, None, "acacia_planks", None, "stick", None,
                       "stick", None, None],
            "layout_options": [
                None, None,
                ["acacia_planks", "bamboo_planks", "birch_planks", "cherry_planks",
                 "crimson_planks", "dark_oak_planks", "jungle_planks",
                 "mangrove_planks", "oak_planks", "pale_oak_planks",
                 "spruce_planks", "warped_planks"],
                None, ["stick"], None, ["stick"], None, None,
            ],
            "ordered": True, "output_count": 1, "station": "crafting_grid",
        },
        "span_text": "{{Crafting|C1={Any Planks}|B2=Stick|A3=Stick|Output=Wooden Spear}}",
    }
    expected = MechanismOperator(
        "cf_0d210ee00d1c6174", "craft_grid",
        {"layout": ["acacia_planks", None, None, None, "stick", None,
                    None, None, "stick"]},
        (("acacia_planks", "bamboo_planks", "birch_planks", "cherry_planks",
          "crimson_planks", "dark_oak_planks", "jungle_planks", "mangrove_planks",
          "oak_planks", "pale_oak_planks", "spruce_planks", "warped_planks"),
         ("stick",), ("stick",)),
        "wooden_spear", layout_slots=(0, 4, 8),
    )
    assert _operator(claim) == [expected]


def test_real_deterministic_prose_claim_matches_g1_operator_field_by_field():
    claim = {
        "claim_id": "claim_7dfb231ba04e42e79d36", "claim_type": "drop",
        "subject_item": "oak_log",
        "payload": {"drops": ["oak_log"], "extractor": "deterministic_prose",
                    "silk_touch": False},
        "span_text": "Logs drop themselves when broken with any tool.",
    }
    expected = MechanismOperator(
        "cf_6c1f151e3911f2a7", "mine", {"node_type": "oak_log"}, (), "oak_log",
    )
    assert _operator(claim) == [expected]


def test_real_llm_claim_matches_g1_operator_field_by_field():
    claim = {
        "claim_id": "claim_5fc37fc93cbbcc3f6d29", "claim_type": "recipe",
        "subject_item": "chest_minecart",
        "payload": {"ingredients": ["chest", "minecart"], "layout": None,
                    "output_count": 1, "station": "crafting_grid"},
        "span_text": "Crafting recipe for Minecart with Chest: Chest, Minecart",
    }
    expected = MechanismOperator(
        "cf_57ebd937febbf42a", "craft_grid",
        {"layout": ["chest", "minecart", None, None, None, None, None, None, None]},
        (("chest",), ("minecart",)), "chest_minecart", layout_slots=(0, 1),
    )
    assert _operator(claim) == [expected]


def test_real_smelting_alternatives_expand_to_distinct_g1_operators():
    claim = {
        "claim_id": "claim_ca899ca0090d62f962b2", "claim_type": "smelting",
        "subject_item": "iron_ingot",
        "payload": {"ingredients": ["iron_ore", "deepslate_iron_ore"],
                    "layout": None, "output_count": 1, "station": "furnace"},
        "span_text": "Iron Ingot: inputs Iron Ore; Deepslate Iron Ore",
    }
    assert _operator(claim) == [
        MechanismOperator(
            "cf_c3f3cb3dd99adf45", "smelt",
            {"input": "iron_ore", "fuel": "coal", "n": 1},
            (("iron_ore",),), "iron_ingot", requires=("coal",), layout_slots=(0,),
        ),
        MechanismOperator(
            "cf_dcf694d1562b3335", "smelt",
            {"input": "deepslate_iron_ore", "fuel": "coal", "n": 1},
            (("deepslate_iron_ore",),), "iron_ingot", requires=("coal",),
            layout_slots=(0,),
        ),
    ]


def test_real_repeated_drop_claim_keeps_quantity_outside_g1_identity():
    claim = {
        "claim_id": "claim_dcfeecc34695b3243aae", "claim_type": "drop",
        "subject_item": "redstone_ore",
        "payload": {"drops": ["redstone"] * 4, "silk_touch": False},
        "span_text": "When mined, it drops 4-5 redstone dust.",
    }
    public_text = "Redstone ore must be mined with an iron pickaxe or higher."
    expected = MechanismOperator(
        "cf_6302c28939a2596e", "mine", {"node_type": "redstone_ore"}, (),
        "redstone", output_count=4, requires=("iron_pickaxe",),
    )
    assert _operator(claim, public_text) == [expected]


def test_corpus_normalizer_mapping_handles_display_names_and_aliases():
    normalizer, provenance = _compiler(WEBCRAFT)
    assert normalizer.item_id("Iron Ingot") == "iron_ingot"
    assert normalizer.item_id("Minecart with Chest") == "chest_minecart"
    assert normalizer.item_id("iron_chain") == "chain"
    assert len(provenance["normalizer_sources"]) == 2


def test_family_parent_suppresses_redundant_normalized_child_operator():
    from arbiter.evaluation.p6_execution import _claims

    parent = {
        "claim_id": "parent", "parent_claim_id": None, "chunk_id": "chunk",
        "claim_type": "recipe", "subject_item": "wooden_spear",
        "payload": {
            "ingredients": ["oak_planks", "stick", "stick"],
            "layout": ["oak_planks", None, None, None, "stick", None,
                       None, None, "stick"],
            "layout_options": [
                ["oak_planks", "spruce_planks"], None, None, None,
                ["stick"], None, None, None, ["stick"],
            ],
            "output_count": 1,
        },
        "polarity": "positive", "tense": "current", "span_text": "Any Planks",
    }
    child = {
        **parent, "claim_id": "child", "parent_claim_id": "parent",
        "payload": {
            "ingredients": ["oak_planks", "stick", "stick"],
            "layout": ["oak_planks", None, None, None, "stick", None,
                       None, None, "stick"],
            "output_count": 1,
        },
    }
    hit = {
        "text": "Any Planks", "source_site": "wiki", "page_title": "Spear",
        "revid": "1", "timestamp": "2026-01-01", "chunk_id": "chunk",
    }
    derived, operators = _claims({
        "search_rows": [hit], "claims": [parent, child], "mineable_nodes": [],
    })
    assert len(derived) == len(operators) == 1
    assert operators[0].input_options[0] == ("oak_planks", "spruce_planks")


def test_retrieval_matcher_treats_occupied_and_empty_slots_as_incompatible():
    import importlib.util

    path = Path(__file__).parents[1] / "scripts/analyze_p6_round5.py"
    spec = importlib.util.spec_from_file_location("analyze_p6_round5_test", path)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    operator = MechanismOperator(
        "m", "craft_grid", {"layout": ["stick"] + [None] * 8},
        (("stick",),), "torch", layout_slots=(0,),
    )
    claim = {
        "claim_type": "recipe", "subject_item": "torch",
        "polarity": "positive", "tense": "current",
        "payload": {
            "ingredients": ["stick", "coal"],
            "layout": ["stick", "coal"] + [None] * 7,
            "output_count": 1,
        },
    }
    assert module._retrieval_matches(operator, claim) is False


def test_public_drop_text_supplies_explicit_tool_and_quantity_metadata():
    assertion = _claim_assertion({
        "claim_type": "drop",
        "subject_item": "redstone_ore",
        "payload": {
            "drops": ["redstone", "redstone", "redstone", "redstone"],
            "silk_touch": False,
        },
        "span_text": "Redstone ore drops four redstone.",
    }, "Redstone ore must be mined with an iron pickaxe or higher.")
    operator = operator_from_assertion(assertion)
    assert operator.requires == ("iron_pickaxe",)
    assert operator.output_count == 4


def test_planning_gap_reports_missing_prerequisite():
    operator = operator_from_assertion({
        "kind": "recipe", "station": "crafting_grid", "ordered": False,
        "ingredients": [["oak_planks"]], "output": "stick", "count": 4,
        "recipe_type": "crafting_shapeless",
    })
    assert _planning_gap_items([operator], Counter(), "stick") == ["oak_planks"]


def test_non_resource_drop_is_not_compiled_as_mineable_node():
    raw = {
        "claim_type": "drop", "subject_item": "iron_ingot",
        "payload": {"drops": ["iron_ingot"], "silk_touch": False},
        "span_text": "Iron golems drop iron ingots.",
    }
    assert _claim_assertion(raw, raw["span_text"], {"iron_ore"}) is None


def test_reader_v5_queries_display_family_and_id_in_order():
    reader = {
        "query_sequence": ["display_name", "family_name", "id"],
        "family_table": [
            {"match": "suffix", "value": "_chest_boat", "query": "chest boat"},
            {"match": "suffix", "value": "_boat", "query": "boat"},
        ],
    }
    assert _family_query("oak_chest_boat", reader["family_table"]) == "chest boat"
    assert _demand_queries("oak_chest_boat", reader) == [
        ("display_name", "oak chest boat"),
        ("family_name", "chest boat"),
        ("id", "minecraft:oak_chest_boat"),
    ]
    assert _demand_queries("iron_ingot", reader) == [
        ("display_name", "iron ingot"),
        ("id", "minecraft:iron_ingot"),
    ]


def test_effective_extractor_matches_v62_counting_rule():
    assert _effective_extractor({
        "payload": {"extractor": "deterministic_prose"},
        "extractor_version": "96b07e80",
    }, "96b07e80") == "deterministic_prose"
    assert _effective_extractor({
        "payload": {}, "extractor_version": "96b07e80",
    }, "96b07e80") == "llm"
    assert _effective_extractor({
        "payload": {}, "extractor_version": "other",
    }, "96b07e80") == "unclassified"


def test_all_probe_declaration_preserves_all_htr_and_total_cost():
    events = [
        {"schema_version": 2, "method": "mine", "intent": "probe",
         "cost": {"primary": 2}},
        {"schema_version": 2, "method": "craft_grid", "intent": "probe",
         "cost": {"primary": 3}},
    ]
    vectors = {
        "cf_a": {"1.16.5": True, "1.19": True, "1.21.11": False},
        "cf_b": {"1.16.5": True, "1.19": False, "1.21.11": False},
    }
    result = _action_role_metrics(
        events, ["cf_a", "cf_b"], "1.21.11", vectors,
    )
    assert result["HTR_commit"] == 0
    assert result["HTR_all"] == 1
    assert result["probe_count"] == 2
    assert result["probe_cost"] == result["primary_cost"] == 5


class _SeparatorApi:
    def __init__(self, *, raw_iron, turtle=False):
        self.raw_iron = raw_iron
        self.turtle = turtle
        self.calls = []

    def mine(self, node_type, *, intent=None):
        self.calls.append(("mine", node_type, intent))
        if node_type == "stone":
            return {"success": True, "items": [{"name": "cobblestone"}]}
        item = "raw_iron" if self.raw_iron else "iron_ore"
        return {"success": True, "items": [{"name": item}]}

    def craft_grid(self, layout, *, intent=None):
        self.calls.append(("craft_grid", tuple(layout), intent))
        return {"success": True, "product": {"name": "stone_pickaxe"}}

    def check_name(self, name, *, intent=None):
        self.calls.append(("check_name", name, intent))
        return {"resolvable": self.turtle}


def test_probe_then_rag_separator_tree_identifies_all_versions():
    old = _SeparatorApi(raw_iron=False)
    middle = _SeparatorApi(raw_iron=True, turtle=False)
    new = _SeparatorApi(raw_iron=True, turtle=True)
    assert _probe_then_rag_identify(old)["identified_version"] == "1.16.5"
    assert _probe_then_rag_identify(middle)["identified_version"] == "1.19"
    assert _probe_then_rag_identify(new)["identified_version"] == "1.21.11"
    assert len(old.calls) == 3
    assert len(middle.calls) == len(new.calls) == 4
    assert all(call[-1] == "probe" for api in (old, middle, new) for call in api.calls)


def test_probe_then_rag_temporal_filter_keeps_linked_claims_only():
    bundle = {
        "search_rows": [
            {"chunk_id": "old", "timestamp": "2021-02-01T00:00:00Z"},
            {"chunk_id": "new", "timestamp": "2022-06-01T00:00:00Z"},
        ],
        "claims": [
            {"chunk_id": "old", "claim_id": "a"},
            {"chunk_id": "new", "claim_id": "b"},
        ],
    }
    filtered = _probe_then_rag_filter_bundle(bundle, "1.16.5")
    assert [row["chunk_id"] for row in filtered["search_rows"]] == ["old"]
    assert [row["claim_id"] for row in filtered["claims"]] == ["a"]
    assert filtered["temporal_filter"]["corpus_cutoff"] == (
        "2021-02-13T16:05:32+00:00"
    )
