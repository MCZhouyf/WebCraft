"""Deterministic Wiki-template claim extraction and truth-vector annotation."""
from __future__ import annotations

import argparse
import hashlib
import html
import json
import re
from collections import Counter
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterator


ROOT = Path(__file__).resolve().parents[1]
VERSIONS = ("1.16.5", "1.19", "1.21.11")
SLOTS = ("A1", "B1", "C1", "A2", "B2", "C2", "A3", "B3", "C3")
COLORS = (
    "white",
    "orange",
    "magenta",
    "light_blue",
    "yellow",
    "lime",
    "pink",
    "gray",
    "light_gray",
    "cyan",
    "purple",
    "blue",
    "brown",
    "green",
    "red",
    "black",
)

DISPLAY_ALIASES = {
    "{any planks}": "oak_planks",
    "{any stone tier block}": "cobblestone",
    "any planks": "oak_planks",
    "block of coal": "coal_block",
    "block of diamond": "diamond_block",
    "block of gold": "gold_block",
    "block of iron": "iron_block",
    "block of lapis lazuli": "lapis_block",
    "block of raw gold": "raw_gold_block",
    "block of raw iron": "raw_iron_block",
    "block of redstone": "redstone_block",
    "minecart with chest": "chest_minecart",
    "minecart with furnace": "furnace_minecart",
    "minecart with hopper": "hopper_minecart",
    "matching boat": "oak_boat",
    "matching boat with chest": "oak_chest_boat",
    "matching overworld planks": "oak_planks",
    "matching planks": "oak_planks",
    "matching wooden slab": "oak_slab",
    "matching wood slab": "oak_slab",
    "boat with chest": "oak_chest_boat",
    "leather tunic": "leather_chestplate",
    "leather cap": "leather_helmet",
    "redstone dust": "redstone",
    "slimeball": "slime_ball",
    "vines": "vine",
    "wood planks": "oak_planks",
}


def normalize_display(value: str) -> str:
    value = html.unescape(value)
    value = re.sub(r"<!--.*?-->", "", value, flags=re.S)
    value = re.sub(r"<[^>]+>", "", value)
    value = re.sub(r"\[\[([^]|]+)\|([^]]+)\]\]", r"\2", value)
    value = re.sub(r"\[\[([^]]+)\]\]", r"\1", value)
    value = re.sub(r"\{\{[^{}]*\}\}", "", value)
    value = value.replace("'''", "").replace("''", "")
    return re.sub(r"\s+", " ", value).strip()


def split_top_level(value: str, separator: str = "|") -> list[str]:
    parts: list[str] = []
    start = 0
    braces = brackets = 0
    index = 0
    while index < len(value):
        pair = value[index : index + 2]
        if pair == "{{":
            braces += 1
            index += 2
            continue
        if pair == "}}" and braces:
            braces -= 1
            index += 2
            continue
        if pair == "[[":
            brackets += 1
            index += 2
            continue
        if pair == "]]" and brackets:
            brackets -= 1
            index += 2
            continue
        if value[index] == separator and braces == 0 and brackets == 0:
            parts.append(value[start:index])
            start = index + 1
        index += 1
    parts.append(value[start:])
    return parts


def iter_templates(text: str) -> Iterator[tuple[int, str]]:
    stack: list[int] = []
    index = 0
    while index < len(text) - 1:
        pair = text[index : index + 2]
        if pair == "{{":
            stack.append(index)
            index += 2
            continue
        if pair == "}}" and stack:
            start = stack.pop()
            if not stack:
                yield start, text[start + 2 : index]
            index += 2
            continue
        index += 1


def parse_template(body: str) -> tuple[str, list[str], dict[str, str]]:
    parts = split_top_level(body)
    name = normalize_display(parts[0]).casefold()
    positional: list[str] = []
    named: dict[str, str] = {}
    for part in parts[1:]:
        key_value = split_top_level(part, "=")
        if len(key_value) > 1:
            key = key_value[0].strip()
            named[key.casefold()] = "=".join(key_value[1:]).strip()
        else:
            positional.append(part.strip())
    return name, positional, named


class NameResolver:
    def __init__(self) -> None:
        self.ids: set[str] = set()
        self.ids_by_version: dict[str, set[str]] = {}
        for version in VERSIONS:
            truth = json.loads((ROOT / f"truth/{version}.json").read_text())
            version_ids = {
                x.removeprefix("minecraft:") for x in truth["names"]["items"]
            }
            self.ids_by_version[version] = version_ids
            self.ids.update(version_ids)
        self.aliases = {identifier.replace("_", " "): identifier for identifier in self.ids}
        self.aliases.update(DISPLAY_ALIASES)

    def resolve(self, value: str) -> str | None:
        cleaned = normalize_display(value)
        cleaned = re.split(r"\s*[,;]\s*", cleaned, maxsplit=1)[0]
        key = cleaned.casefold().replace("-", " ")
        key = re.sub(r"\s+", " ", key).strip()
        return self.aliases.get(key)

    def resolve_options(self, value: str, version: str | None = None) -> list[str]:
        cleaned = normalize_display(value)
        key = re.sub(r"\s+", " ", cleaned.casefold().replace("-", " ")).strip()
        if key in {"any planks", "{any planks}"} and version in self.ids_by_version:
            return sorted(
                identifier
                for identifier in self.ids_by_version[version]
                if identifier.endswith("_planks")
            )
        if key in {
            "any stone tier block",
            "{any stone tier block}",
            "any cobblestone or blackstone",
            "{any cobblestone or blackstone}",
        } and version in self.ids_by_version:
            return sorted(
                identifier
                for identifier in ("blackstone", "cobbled_deepslate", "cobblestone")
                if identifier in self.ids_by_version[version]
            )
        if key in {"any wood slab", "any wooden slab"} and version in self.ids_by_version:
            planks = {
                identifier.removesuffix("_planks")
                for identifier in self.ids_by_version[version]
                if identifier.endswith("_planks")
            }
            return sorted(
                f"{wood}_slab"
                for wood in planks
                if f"{wood}_slab" in self.ids_by_version[version]
            )
        resolved = self.resolve(value)
        return [resolved] if resolved else []

    def is_registered(self, value: str, version: str) -> bool:
        return value in self.ids_by_version[version]


def output_value(value: str, resolver: NameResolver) -> tuple[str | None, int]:
    cleaned = normalize_display(value)
    first = re.split(r"\s*;\s*", cleaned, maxsplit=1)[0]
    match = re.match(r"^(.*?)(?:\s*,\s*(\d+))?$", first)
    if not match:
        return resolver.resolve(first), 1
    return resolver.resolve(match.group(1)), int(match.group(2) or 1)


def parse_crafting(
    positional: list[str],
    named: dict[str, str],
    resolver: NameResolver,
    version: str | None = None,
) -> dict[str, Any] | None:
    output, count = output_value(named.get("output", ""), resolver)
    if not output:
        return None
    has_named_slots = any(slot.casefold() in named for slot in SLOTS)
    is_shapeless = normalize_display(named.get("shapeless", "")).casefold() in {
        "1",
        "true",
        "yes",
    }
    if has_named_slots and not is_shapeless:
        layout_options = [
            options or None
            for slot in SLOTS
            if (options := resolver.resolve_options(named.get(slot.casefold(), ""), version)) is not None
        ]
        layout = [options[0] if options else None for options in layout_options]
        ingredients = [item for item in layout if item]
        ordered: bool | None = True
    else:
        ignored = {"", "-"}
        raw = positional + [
            named[slot.casefold()]
            for slot in SLOTS
            if slot.casefold() in named
        ]
        raw = [value for value in raw if normalize_display(value) not in ignored]
        ingredients = [item for value in raw if (item := resolver.resolve(value))]
        layout = None
        layout_options = None
        ordered = None
    if not ingredients:
        return None
    return {
        "ingredients": ingredients,
        "layout": layout,
        "layout_options": layout_options,
        "output_count": count,
        "ordered": ordered,
        "station": "crafting_grid",
    }


def parse_crafting_variants(
    positional: list[str],
    named: dict[str, str],
    resolver: NameResolver,
    version: str | None = None,
) -> list[tuple[str, dict[str, Any]]]:
    outputs = [value.strip() for value in split_top_level(named.get("output", ""), ";")]
    generic_output = re.sub(
        r"\s*,\s*\d+$", "", normalize_display(named.get("output", ""))
    ).casefold()
    raw_inputs = "; ".join(positional + [named.get(slot.casefold(), "") for slot in SLOTS])
    if "matching" in normalize_display(raw_inputs).casefold() and "planks" in generic_output:
        registry = resolver.ids_by_version.get(version, resolver.ids)
        output_ids = []
        if generic_output in {"matching overworld planks", "matching nether planks"}:
            nether = "nether" in generic_output
            for identifier in sorted(registry):
                if not identifier.endswith("_planks") or identifier == "bamboo_planks":
                    continue
                prefix = identifier.removesuffix("_planks")
                is_nether = prefix in {"crimson", "warped"}
                if is_nether == nether:
                    output_ids.append(identifier)
        else:
            output_ids = [
                output for raw in outputs
                if (output := output_value(raw, resolver)[0]) and output.endswith("_planks")
            ]
        markers = normalize_display(raw_inputs).casefold()
        variants = []
        for output in output_ids:
            prefix = output.removesuffix("_planks")
            candidates = []
            for marker, identifier in (
                ("matching stripped log", f"stripped_{prefix}_log"),
                ("matching log", f"{prefix}_log"),
                ("matching stripped wood", f"stripped_{prefix}_wood"),
                ("matching wood", f"{prefix}_wood"),
                ("matching stripped stem", f"stripped_{prefix}_stem"),
                ("matching stem", f"{prefix}_stem"),
                ("matching stripped hyphae", f"stripped_{prefix}_hyphae"),
                ("matching hyphae", f"{prefix}_hyphae"),
            ):
                if marker in markers and identifier in registry:
                    candidates.append(identifier)
            raw_output = next(
                (raw for raw in outputs if output_value(raw, resolver)[0] == output),
                named.get("output", ""),
            )
            _, count = output_value(raw_output, resolver)
            for ingredient in dict.fromkeys(candidates):
                variants.append((output, {
                    "ingredients": [ingredient],
                    "layout": None,
                    "layout_options": None,
                    "output_count": count,
                    "ordered": None,
                    "station": "crafting_grid",
                }))
        if variants:
            return variants
    matching_families = {
        "matching dyed wool": "wool",
        "matching wool": "wool",
        "matching dyed carpet": "carpet",
        "matching carpet": "carpet",
        "matching harness": "harness",
    }
    if len(outputs) == 1 and generic_output in matching_families:
        family = matching_families[generic_output]
        registry = resolver.ids_by_version.get(version, resolver.ids)
        count_match = re.search(r"\s*,\s*(\d+)\s*$", named.get("output", ""))
        count_suffix = f",{count_match.group(1)}" if count_match else ""
        values = positional + [named.get(slot.casefold(), "") for slot in SLOTS]
        colored_dye_only = any(
            normalize_display(value).casefold() == "matching colored dye"
            for value in values
        )
        variants = []
        for color in COLORS:
            if color == "white" and colored_dye_only:
                continue
            output = f"{color}_{family}"
            if output not in registry:
                continue
            generic_input = f"any {family}"
            has_any_family = any(
                normalize_display(value).casefold() == generic_input for value in values
            )
            source_colors = (
                [
                    source
                    for source in COLORS
                    if source != color and f"{source}_{family}" in registry
                ]
                if has_any_family
                else [None]
            )
            for source_color in source_colors:
                def replace(value: str) -> str:
                    key = normalize_display(value).casefold()
                    if key in {"matching dye", "matching colored dye"}:
                        return color.replace("_", " ").title() + " Dye"
                    if key == "matching wool" and family in {"carpet", "harness"}:
                        return color.replace("_", " ").title() + " Wool"
                    if key == generic_input and source_color:
                        return source_color.replace("_", " ").title() + " " + family.title()
                    return value

                variant_positional = [replace(value) for value in positional]
                variant_named = dict(named)
                variant_named["output"] = (
                    color.replace("_", " ").title() + " " + family.title() + count_suffix
                )
                for slot in SLOTS:
                    key = slot.casefold()
                    if key in variant_named:
                        variant_named[key] = replace(variant_named[key])
                payload = parse_crafting(
                    variant_positional, variant_named, resolver, version
                )
                if payload:
                    variants.append((output, payload))
        return variants
    if len(outputs) <= 1:
        payload = parse_crafting(positional, named, resolver, version)
        output, _ = output_value(named.get("output", ""), resolver)
        return [(output, payload)] if output and payload else []
    variants = []
    for index, raw_output in enumerate(outputs):
        variant = dict(named)
        variant["output"] = raw_output
        for slot in SLOTS:
            key = slot.casefold()
            choices = split_top_level(named.get(key, ""), ";")
            if len(choices) == len(outputs):
                variant[key] = choices[index].strip()
        payload = parse_crafting(positional, variant, resolver, version)
        output, _ = output_value(raw_output, resolver)
        if output and payload:
            variants.append((output, payload))
    return variants


def parse_smelting(
    positional: list[str], named: dict[str, str], resolver: NameResolver
) -> tuple[str, dict[str, Any]] | None:
    values = [value for value in positional if normalize_display(value)]
    if len(values) < 2:
        return None
    inputs = [resolver.resolve(value) for value in re.split(r"\s*;\s*", values[0])]
    output, count = output_value(values[1], resolver)
    inputs = [value for value in inputs if value]
    if not inputs or not output:
        return None
    return output, {
        "ingredients": inputs,
        "output_count": count,
        "station": "furnace",
    }


def parse_smelting_variants(
    positional: list[str], named: dict[str, str], resolver: NameResolver
) -> list[tuple[str, dict[str, Any]]]:
    values = [value for value in positional if normalize_display(value)]
    if len(values) < 2:
        return []
    inputs = split_top_level(values[0], ";")
    outputs = split_top_level(values[1], ";")
    if len(inputs) != len(outputs) or len(outputs) <= 1:
        parsed = parse_smelting(positional, named, resolver)
        return [parsed] if parsed else []
    variants = []
    for raw_input, raw_output in zip(inputs, outputs):
        parsed = parse_smelting([raw_input, raw_output], named, resolver)
        if parsed:
            variants.append(parsed)
    return variants


@lru_cache(maxsize=3)
def normalized_truth_recipes(version: str) -> list[dict[str, Any]]:
    truth = json.loads((ROOT / f"truth/{version}.json").read_text())
    recipes = []
    for recipe in truth["recipes"]:
        output = (recipe.get("result", {}).get("item") or "").removeprefix("minecraft:")
        if not output:
            continue
        if recipe.get("ordered"):
            layout: list[set[str] | None] = [None] * 9
            for row_index, row in enumerate(recipe.get("pattern", [])):
                for col_index, symbol in enumerate(row):
                    if symbol != " ":
                        layout[row_index * 3 + col_index] = {
                            value.removeprefix("minecraft:")
                            for value in recipe.get("key", {}).get(symbol, [])
                        }
            ingredients = None
        else:
            layout = None
            ingredients = [
                {value.removeprefix("minecraft:") for value in options}
                for options in recipe.get("ingredients", [])
            ]
        recipes.append(
            {
                "output": output,
                "count": int(recipe.get("result", {}).get("count", 1)),
                "station": recipe.get("station"),
                "ordered": bool(recipe.get("ordered")),
                "layout": layout,
                "ingredients": ingredients,
            }
        )
    return recipes


def multiset_options_match(items: list[str], slots: list[set[str]]) -> bool:
    if len(items) != len(slots):
        return False
    remaining = list(slots)
    for item in sorted(items, key=lambda x: sum(x in slot for slot in remaining)):
        for index, slot in enumerate(remaining):
            if item in slot:
                remaining.pop(index)
                break
        else:
            return False
    return True


def trim_layout(layout: list[Any]) -> tuple[tuple[Any, ...], ...]:
    """Remove empty outer rows/columns so translated shaped recipes compare equal."""
    rows = [layout[index : index + 3] for index in range(0, 9, 3)]
    occupied_rows = [index for index, row in enumerate(rows) if any(x is not None for x in row)]
    occupied_cols = [
        index for index in range(3) if any(row[index] is not None for row in rows)
    ]
    if not occupied_rows or not occupied_cols:
        return ()
    return tuple(
        tuple(rows[row][col] for col in range(min(occupied_cols), max(occupied_cols) + 1))
        for row in range(min(occupied_rows), max(occupied_rows) + 1)
    )


def recipe_truth(claim: dict[str, Any], recipes: list[dict[str, Any]]) -> bool:
    payload = claim["payload"]
    candidates = [
        recipe
        for recipe in recipes
        if recipe["output"] == claim["subject_item"]
        and recipe["count"] == payload["output_count"]
        and recipe["station"] == payload["station"]
    ]
    if (
        payload.get("station") == "furnace"
        and payload.get("layout") is None
        and payload["ingredients"]
        and all(
        any(
            recipe["ingredients"] is not None
            and len(recipe["ingredients"]) == 1
            and item in recipe["ingredients"][0]
            for recipe in candidates
        )
        for item in payload["ingredients"]
        )
    ):
        return True
    for recipe in candidates:
        if payload.get("layout") is not None:
            if recipe["layout"] is None:
                continue
            source_layout = payload.get("layout_options") or [
                [item] if item is not None else None for item in payload["layout"]
            ]
            actual_layout = trim_layout(source_layout)
            expected_layout = trim_layout(recipe["layout"])
            if len(actual_layout) != len(expected_layout) or any(
                len(actual_row) != len(expected_row)
                for actual_row, expected_row in zip(actual_layout, expected_layout)
            ):
                continue
            if all(
                (not actual)
                if options is None
                else bool(actual)
                and (
                    set(actual) == set(options)
                    if len(actual) > 1
                    else actual[0] in options
                )
                for actual_row, expected_row in zip(actual_layout, expected_layout)
                for actual, options in zip(actual_row, expected_row)
            ):
                return True
        else:
            slots = recipe["ingredients"]
            if slots is None:
                if payload.get("station") == "crafting_grid":
                    shaped_slots = [options for options in recipe["layout"] if options]
                    if (
                        len(set(payload["ingredients"])) == 1
                        and multiset_options_match(payload["ingredients"], shaped_slots)
                    ):
                        return True
                    continue
                slots = [options for options in recipe["layout"] if options]
            if len(slots) == 1 and all(item in slots[0] for item in payload["ingredients"]):
                return True
            if multiset_options_match(payload["ingredients"], slots):
                return True
    return False


def claim_id(claim: dict[str, Any]) -> str:
    body = json.dumps(claim, sort_keys=True, separators=(",", ":"))
    return "gold_" + hashlib.sha256(body.encode()).hexdigest()[:16]


def annotate_claim(
    claim: dict[str, Any],
    source: dict[str, Any],
    resolver: NameResolver,
    recipes_by_version: dict[str, list[dict[str, Any]]],
) -> dict[str, Any]:
    timepoint = source.get("timepoint")
    if (
        claim["tense"] == "current"
        and timepoint in VERSIONS
        and claim["subject_item"]
        and not resolver.is_registered(claim["subject_item"], timepoint)
    ):
        claim["tense"] = "future"
    if claim["claim_type"] in {"recipe", "smelting"}:
        claim["truth_vector"] = {
            version: recipe_truth(claim, recipes_by_version[version])
            for version in VERSIONS
        }
    elif claim["claim_type"] == "drop" and not claim["payload"].get("conditional_variants"):
        vector = {}
        for version in VERSIONS:
            truth = json.loads((ROOT / f"truth/{version}.json").read_text())
            rows = [row for row in truth["drops"]
                    if row["block"].removeprefix("minecraft:") == claim["subject_item"]]
            key = "silk_touch_items" if claim["payload"].get("silk_touch") else "no_silk_touch_items"
            expected = {item.removeprefix("minecraft:") for row in rows for item in row[key]}
            vector[version] = set(claim["payload"]["drops"]) == expected if rows else None
        claim["truth_vector"] = vector
    else:
        claim["truth_vector"] = {version: None for version in VERSIONS}
    claim["claim_id"] = claim_id(claim)
    return claim


def extract_gold(
    text: str, source: dict[str, Any], resolver: NameResolver, recipes_by_version
) -> list[dict[str, Any]]:
    from corpus.block_templates import extract_block_claims

    claims: list[dict[str, Any]] = [
        annotate_claim({**claim, "source": source}, source, resolver, recipes_by_version)
        for claim in extract_block_claims(text, source["page_title"], resolver)
    ]
    history_heading = re.search(r"^==+\s*History\s*==+\s*$", text, re.I | re.M)
    history_start = history_heading.start() if history_heading else len(text) + 1
    for offset, body in iter_templates(text):
        name, positional, named = parse_template(body)
        claim: dict[str, Any] | None = None
        if name == "crafting":
            description = named.get("description", "").casefold()
            if any(
                marker in description
                for marker in ("{{only|bedrock", "{{only|be", "{{only|education")
            ):
                continue
            source_version = source.get("timepoint")
            if source_version == "current":
                source_version = "1.21.11"
            for output, payload in parse_crafting_variants(
                positional, named, resolver, source_version
            ):
                generated = {
                    "source": source,
                    "tense": (
                        "historical"
                        if offset >= history_start
                        else "future"
                        if "{{upcoming" in description or "{{onlyupcoming" in description
                        else "current"
                    ),
                    "claim_type": "recipe",
                    "subject_item": output,
                    "payload": payload,
                    "polarity": "positive",
                }
                claims.append(
                    annotate_claim(generated, source, resolver, recipes_by_version)
                )
            continue
        elif name == "smelting":
            for output, payload in parse_smelting_variants(positional, named, resolver):
                generated = {
                    "source": source,
                    "tense": "historical" if offset >= history_start else "current",
                    "claim_type": "smelting",
                    "subject_item": output,
                    "payload": payload,
                    "polarity": "positive",
                }
                claims.append(
                    annotate_claim(generated, source, resolver, recipes_by_version)
                )
            continue
        elif name == "lootchestitem" and positional:
            item = resolver.resolve(positional[0])
            if item:
                claim = {
                    "source": source,
                    "tense": "current",
                    "claim_type": "chest_loot",
                    "subject_item": item,
                    "payload": {"location_template": normalize_display(positional[0])},
                    "polarity": "positive",
                }
        elif name == "history":
            prose = " ".join(normalize_display(value) for value in positional + list(named.values()))
            lower = prose.casefold()
            if any(word in lower for word in ("craft", "recipe", "smelt", "drop")):
                kind = "historical_recipe" if any(x in lower for x in ("craft", "recipe", "smelt")) else "historical_drop"
                claim = {
                    "source": source,
                    "tense": "historical",
                    "claim_type": kind,
                    "subject_item": resolver.resolve(source["page_title"]),
                    "payload": {"text": prose},
                    "polarity": "negative" if re.search(r"\b(?:no longer|cannot|can't|removed)\b", lower) else "positive",
                }
        if claim:
            claims.append(annotate_claim(claim, source, resolver, recipes_by_version))
    return claims


def write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in rows))


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--snapshot-manifest", type=Path, required=True)
    parser.add_argument("--raw-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    snapshots = json.loads(args.snapshot_manifest.read_text())["snapshots"]
    resolver = NameResolver()
    recipes = {version: normalized_truth_recipes(version) for version in VERSIONS}
    summary = {}
    for version, pages in snapshots.items():
        claims = []
        for page_title, metadata in pages.items():
            source = {
                "site": metadata["source_site"],
                "page_title": page_title,
                "revid": metadata["revid"],
                "timestamp": metadata["timestamp"],
                "timepoint": version,
            }
            text = (args.raw_root / metadata["wikitext"]).read_text(encoding="utf-8")
            claims.extend(extract_gold(text, source, resolver, recipes))
        output = args.output_dir / f"claims_gold_{'t1' if version == '1.16.5' else 't2'}.jsonl"
        write_jsonl(output, claims)
        mismatches = [
            claim
            for claim in claims
            if claim["truth_vector"].get(version) is False
            and claim["claim_type"] in {"recipe", "smelting"}
            and claim["tense"] == "current"
        ]
        write_jsonl(args.output_dir / f"web_truth_mismatches_{version}.jsonl", mismatches)
        summary[version] = {
            "claims": len(claims),
            "by_type": dict(Counter(claim["claim_type"] for claim in claims)),
            "structured_mismatches": len(mismatches),
        }
    print(json.dumps(summary, sort_keys=True))


if __name__ == "__main__":
    main()
