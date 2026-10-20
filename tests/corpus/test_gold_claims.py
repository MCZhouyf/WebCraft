from corpus.gold_claims import (
    NameResolver,
    extract_gold,
    parse_crafting,
    parse_crafting_variants,
    parse_smelting,
    parse_smelting_variants,
    parse_template,
    recipe_truth,
    split_top_level,
    trim_layout,
)


def test_split_top_level_preserves_nested_markup():
    assert split_top_level("Crafting|A1={{tag|x|y}}|Output=[[Block|Stone]]") == [
        "Crafting",
        "A1={{tag|x|y}}",
        "Output=[[Block|Stone]]",
    ]


def test_parse_named_crafting_recipe():
    resolver = NameResolver()
    name, positional, named = parse_template(
        "Crafting|A1=Iron Ingot|B1=Stick|Output=Iron Pickaxe,2"
    )
    claim = parse_crafting(positional, named, resolver)
    assert name == "crafting"
    assert claim["layout"][:3] == ["iron_ingot", "stick", None]
    assert claim["output_count"] == 2


def test_current_wiki_braced_generic_ingredients_have_representatives():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|C1={Any stone-tier block}|B2=Stick|A3=Stick|Output=Stone Spear"
    )
    claim = parse_crafting(positional, named, resolver)
    assert claim["ingredients"] == ["cobblestone", "stick", "stick"]


def test_furnace_any_cobblestone_or_blackstone_slots_are_resolved():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|A1=Any Cobblestone or Blackstone"
        "|B1=Any Cobblestone or Blackstone|Output=Furnace"
    )
    claim = parse_crafting(positional, named, resolver, "1.16.5")
    assert claim["layout_options"][0] == ["blackstone", "cobblestone"]
    assert claim["layout_options"][1] == ["blackstone", "cobblestone"]


def test_chiseled_bookshelf_any_wooden_slab_tracks_registry():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|A1=Any Planks|A2=Any Wooden Slab|Output=Chiseled Bookshelf"
    )
    old = parse_crafting(positional, named, resolver, "1.16.5")
    new = parse_crafting(positional, named, resolver, "1.21.11")
    assert "bamboo_slab" not in old["layout_options"][3]
    assert "bamboo_slab" in new["layout_options"][3]


def test_matching_dyed_wool_template_expands_color_output():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|White Wool|Matching Colored Dye|Output=Matching Dyed Wool"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.16.5")
    blue = [payload for output, payload in variants if output == "blue_wool"]
    assert len(blue) == 1
    assert blue[0]["ingredients"] == ["white_wool", "blue_dye"]
    assert all(output != "white_wool" for output, _ in variants)


def test_any_wool_matching_dye_expands_without_noop_source():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|Any Wool|Matching Dye|Output=Matching Wool"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.21.11")
    blue = [payload for output, payload in variants if output == "blue_wool"]
    assert len(blue) == 15
    assert all("blue_wool" not in payload["ingredients"] for payload in blue)
    assert ["white_wool", "blue_dye"] in [p["ingredients"] for p in blue]


def test_matching_dyed_carpet_named_slots_preserve_output_count():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|A1=White Carpet|B1=Matching Colored Dye"
        "|Output=Matching Dyed Carpet,8"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.16.5")
    blue = [payload for output, payload in variants if output == "blue_carpet"]
    assert len(blue) == 1
    assert blue[0]["layout"][:2] == ["white_carpet", "blue_dye"]
    assert blue[0]["output_count"] == 8


def test_matching_harness_uses_matching_wool_slot():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|A1=Leather|B1=Leather|C1=Leather|A2=Glass"
        "|B2=Matching Wool|C2=Glass|Output=Matching Harness"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.21.11")
    blue = [payload for output, payload in variants if output == "blue_harness"]
    assert len(blue) == 1
    assert blue[0]["layout"][:6] == [
        "leather",
        "leather",
        "leather",
        "glass",
        "blue_wool",
        "glass",
    ]


def test_matching_harness_dye_template_is_shapeless():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|shapeless=1|A2=Any Harness|B2=Matching Dye"
        "|Output=Matching Harness"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.21.11")
    blue = [payload for output, payload in variants if output == "blue_harness"]
    assert len(blue) == 15
    assert all(payload["layout"] is None for payload in blue)
    assert ["white_harness", "blue_dye"] in [payload["ingredients"] for payload in blue]


def test_any_planks_tracks_the_snapshot_registry():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|B2=Any Planks|B3=Any Planks|Output=Stick,4"
    )
    old = parse_crafting(positional, named, resolver, "1.16.5")
    new = parse_crafting(positional, named, resolver, "1.21.11")
    assert "bamboo_planks" not in old["layout_options"][4]
    assert "bamboo_planks" in new["layout_options"][4]


def test_matching_boat_template_has_canonical_representatives():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|Chest|Matching Boat|Output=Matching Boat with Chest"
    )
    claim = parse_crafting(positional, named, resolver, "1.21.11")
    assert claim["ingredients"] == ["chest", "oak_boat"]


def test_matching_overworld_planks_expand_to_source_supported_variants():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|Matching Log; Matching Stripped Log; Matching Wood; Matching Stripped Wood"
        "|Output=Matching Overworld Planks,4"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.16.5")
    oak = [payload for output, payload in variants if output == "oak_planks"]
    assert {payload["ingredients"][0] for payload in oak} == {
        "oak_log", "stripped_oak_log", "oak_wood", "stripped_oak_wood"
    }
    assert all(payload["output_count"] == 4 for payload in oak)


def test_matching_carpet_expands_matching_wool_by_color():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|A3=Matching Wool|B3=Matching Wool|Output=Matching Carpet,3"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.21.11")
    blue = next(payload for output, payload in variants if output == "blue_carpet")
    assert blue["ingredients"] == ["blue_wool", "blue_wool"]
    assert blue["output_count"] == 3


def test_explicit_plank_outputs_align_with_matching_inputs():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|Matching Log; Matching Stem|Output=Oak Planks,4; Crimson Planks,4"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.21.11")
    pairs = {(output, payload["ingredients"][0]) for output, payload in variants}
    assert ("oak_planks", "oak_log") in pairs
    assert ("crimson_planks", "crimson_stem") in pairs


def test_semicolon_aligned_pickaxe_template_expands_variants():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Crafting|A1={Any Planks}; {Any Cobblestone or Blackstone}"
        "|B1={Any Planks}; {Any Cobblestone or Blackstone}"
        "|C1={Any Planks}; {Any Cobblestone or Blackstone}|B2=Stick|B3=Stick"
        "|Output=Wooden Pickaxe; Stone Pickaxe"
    )
    variants = parse_crafting_variants(positional, named, resolver, "1.16.5")
    assert [output for output, _ in variants] == ["wooden_pickaxe", "stone_pickaxe"]
    assert variants[1][1]["layout_options"][0] == ["blackstone", "cobblestone"]


def test_semicolon_aligned_smelting_template_expands_variants():
    resolver = NameResolver()
    _, positional, named = parse_template(
        "Smelting|Iron Pickaxe; Golden Pickaxe|Iron Nugget; Gold Nugget|0.1"
    )
    variants = parse_smelting_variants(positional, named, resolver)
    assert [(output, payload["ingredients"]) for output, payload in variants] == [
        ("iron_nugget", ["iron_pickaxe"]),
        ("gold_nugget", ["golden_pickaxe"]),
    ]


def test_parse_smelting_recipe():
    resolver = NameResolver()
    _, positional, named = parse_template("Smelting|Iron Ore|Iron Ingot|0.7")
    output, payload = parse_smelting(positional, named, resolver)
    assert output == "iron_ingot"
    assert payload["ingredients"] == ["iron_ore"]


def test_history_claim_is_historical_and_negative():
    resolver = NameResolver()
    text = "== History ==\n{{History||1.0|The old recipe can no longer be crafted.}}"
    recipes = {version: [] for version in ("1.16.5", "1.19", "1.21.11")}
    claims = extract_gold(text, {"page_title": "X"}, resolver, recipes)
    assert claims[0]["tense"] == "historical"
    assert claims[0]["polarity"] == "negative"


def test_bedrock_only_recipe_is_excluded():
    resolver = NameResolver()
    text = "{{Crafting|Cobweb|Output=String,9|description={{only|bedrock}}}}"
    recipes = {version: [] for version in ("1.16.5", "1.19", "1.21.11")}
    assert extract_gold(text, {"page_title": "String"}, resolver, recipes) == []


def test_only_upcoming_recipe_is_future():
    resolver = NameResolver()
    text = "{{Crafting|String|Output=Bundle|description={{OnlyUpcoming}}}}"
    recipes = {version: [] for version in ("1.16.5", "1.19", "1.21.11")}
    claims = extract_gold(
        text, {"page_title": "Bundle", "timepoint": "1.19"}, resolver, recipes
    )
    assert claims[0]["tense"] == "future"


def test_trim_layout_ignores_recipe_translation():
    top_left = ["iron", None, None, "iron", "iron", None, None, None, None]
    bottom_right = [None, None, None, None, "iron", None, None, "iron", "iron"]
    assert trim_layout(top_left) == trim_layout(bottom_right)


def test_recipe_truth_treats_smelting_inputs_as_alternatives():
    claim = {
        "subject_item": "glass",
        "payload": {
            "ingredients": ["sand", "red_sand"],
            "output_count": 1,
            "station": "furnace",
        },
    }
    recipes = [
        {
            "output": "glass",
            "count": 1,
            "station": "furnace",
            "layout": None,
            "ingredients": [{"sand", "red_sand"}],
        }
    ]
    assert recipe_truth(claim, recipes)


def test_recipe_truth_does_not_treat_shaped_recipe_as_shapeless():
    claim = {
        "subject_item": "chest_minecart",
        "payload": {
            "ingredients": ["chest", "minecart"],
            "layout": None,
            "output_count": 1,
            "station": "crafting_grid",
        },
    }
    recipes = [
        {
            "output": "chest_minecart",
            "count": 1,
            "station": "crafting_grid",
            "layout": [None, None, None, None, {"chest"}, None, None, {"minecart"}, None],
            "ingredients": None,
        }
    ]
    assert not recipe_truth(claim, recipes)
