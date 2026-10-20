# Public Claim Payload Schema

This document describes the payloads actually present in the frozen `formal` and
`formal_p1` public claim caches as of reading-mode freeze v6.2. It is an empirical
schema, not a promise that every extractor emits every claim type.

## Route identification and common representation

`extractor` is an effective route label, not a top-level field in contract v2:

- `template`: `payload.extractor == "template"`.
- `deterministic_prose`: `payload.extractor == "deterministic_prose"`.
- `llm`: no payload marker; `extractor_version == "96b07e80"`.

The public top-level contract is `claim_id`, `chunk_id`, `claim_type`,
`subject_item`, `payload`, `polarity`, `tense`, `span_text`, `extractor_version`,
`normalizer_version`, and `parent_claim_id`. Legacy nine-field storage rows are
upgraded to these eleven fields by `ClaimsCache`; this does not change payloads.

Unless a row below says otherwise:

- `subject_item`, ingredient entries, layout entries, and drop entries are canonical
  lower-case snake-case item IDs, not Wiki display names.
- A crafted or smelted product is `subject_item`; `output_count` is its integer
  quantity. A dropped product is an entry in `drops`.
- Repeated IDs encode input or drop quantity. There is no `{id, count}` object.
- A shaped crafting `layout` is a row-major nine-element array for a 3x3 grid;
  `null` is an empty slot. `layout: null` is unshaped or shape-unspecified.
- Family labels such as "Any Planks" are not display-name payloads. Template rows
  represent per-slot alternatives in `layout_options`; normalized child claims select
  concrete IDs and point to the source claim through `parent_claim_id`.

## Template payloads

| Claim type | Exact observed payload key sets | Types and representation |
|---|---|---|
| `recipe` | Direct: `{deterministic, extractor, ingredients, layout, layout_options, ordered, output_count, station}`. Normalized child: `{deterministic, extractor, ingredients, layout, output_count, station}`. | `deterministic: boolean` (always `true`); `extractor: string` (always `template`); `ingredients: array<string>` of IDs with repetition; `layout: array<string|null> | null`; `layout_options: array<array<string>|null> | null`; `ordered: boolean | null`; `output_count: integer`; `station: string` (always `crafting_grid`). When present, both layout arrays have nine slots. |
| `smelting` | `{deterministic, extractor, ingredients, output_count, station}` | Markers as above; `ingredients: array<string>` of IDs; `output_count: integer` (observed `1`); `station: string` (always `furnace`). The output is `subject_item`; there is no `layout` key. |
| `drop` | Not observed in either frozen cache. | The infobox parser supports drops, but the selected source infoboxes supplied no explicit drop field; no empirical frozen payload schema is claimed here. |
| `craftability` | Not observed. | Not emitted by this route in the frozen caches. |
| `id` | Not observed. | Not emitted by this route in the frozen caches. |
| `name` | Not observed. | Not emitted by this route in the frozen caches. |

Alternative ingredients are explicit only in direct `recipe.layout_options`. Each
occupied slot is an array of acceptable canonical IDs; a singleton is a fixed item.
`ingredients` and `layout` contain one selected representative. Family-normalized
children have concrete `ingredients`/`layout` and omit `layout_options` and `ordered`.

### Three real template examples

The following public fields and values are copied verbatim from the cache.

```json
{
  "claim_id": "claim_348f79c14b2af3a612fa",
  "claim_type": "recipe",
  "subject_item": "wooden_spear",
  "payload": {
    "deterministic": true,
    "extractor": "template",
    "ingredients": ["acacia_planks", "stick", "stick"],
    "layout": [null, null, "acacia_planks", null, "stick", null, "stick", null, null],
    "layout_options": [
      null,
      null,
      ["acacia_planks", "bamboo_planks", "birch_planks", "cherry_planks", "crimson_planks", "dark_oak_planks", "jungle_planks", "mangrove_planks", "oak_planks", "pale_oak_planks", "spruce_planks", "warped_planks"],
      null,
      ["stick"],
      null,
      ["stick"],
      null,
      null
    ],
    "ordered": true,
    "output_count": 1,
    "station": "crafting_grid"
  },
  "span_text": "{{Crafting\n|C1={Any Planks}\n|B2=Stick\n|A3=Stick\n|Output=Wooden Spear\n|type=Combat\n}}"
}
```

```json
{
  "claim_id": "claim_152c06a78d78b8ebb58a",
  "claim_type": "recipe",
  "subject_item": "iron_nugget",
  "payload": {
    "deterministic": true,
    "extractor": "template",
    "ingredients": ["iron_ingot"],
    "layout": null,
    "layout_options": null,
    "ordered": null,
    "output_count": 9,
    "station": "crafting_grid"
  },
  "span_text": "{{Crafting\n|Iron Ingot\n|Output= Iron Nugget,9\n|type=Material\n}}"
}
```

```json
{
  "claim_id": "claim_feb739870da82be31cea",
  "claim_type": "smelting",
  "subject_item": "iron_ingot",
  "payload": {
    "deterministic": true,
    "extractor": "template",
    "ingredients": ["iron_ore"],
    "output_count": 1,
    "station": "furnace"
  },
  "span_text": "{{Smelting\n|Iron Ore\n|Iron Ingot\n|0,7\n}}"
}
```

## Deterministic-prose payloads

| Claim type | Exact observed payload key sets | Types and representation |
|---|---|---|
| `recipe` | Not observed. | Not emitted by this route. |
| `smelting` | Not observed. | Not emitted by this route. |
| `drop` | `{drops, extractor, silk_touch}` | `drops: array<string>` of canonical IDs, with repetition for quantity; `extractor: string` (always `deterministic_prose`); `silk_touch: boolean` (always `false` in the frozen caches). `subject_item` is the broken block ID. |
| `craftability` | Not observed. | Not emitted by this route. |
| `id` | Not observed. | Not emitted by this route. |
| `name` | Not observed. | Not emitted by this route. |

This route has no structured family or alternative field. Family-page subjects were
expanded to concrete IDs before storage; the exact source sentence remains in
`span_text`.

### Three real deterministic-prose examples

```json
{"claim_id":"claim_5886e42ed29b4ec1c32b","claim_type":"drop","subject_item":"stone","payload":{"drops":["cobblestone"],"extractor":"deterministic_prose","silk_touch":false},"span_text":"Mining stone now drops cobblestone."}
```

```json
{"claim_id":"claim_0967d436a42db6ae9143","claim_type":"drop","subject_item":"iron_ore","payload":{"drops":["iron_ore"],"extractor":"deterministic_prose","silk_touch":false},"span_text":"Iron ore now drops itself when mined."}
```

```json
{"claim_id":"claim_7dfb231ba04e42e79d36","claim_type":"drop","subject_item":"oak_log","payload":{"drops":["oak_log"],"extractor":"deterministic_prose","silk_touch":false},"span_text":"Logs drop themselves when broken with any tool."}
```

## LLM payloads

LLM rows have no `payload.extractor`. The frozen extractor hash is the route marker.
The mechanism-bearing types are regular; historical metadata types retain several
legacy key spellings exactly as extracted.

| Claim type | Exact observed payload key sets | Types and representation |
|---|---|---|
| `recipe` | `{ingredients, layout, output_count, station}` | `ingredients: array<string>` of canonical IDs with repetition; `layout: array<string|null> | null` (nine slots when non-null); `output_count: integer`; `station: string` (always `crafting_grid`). The product is `subject_item`. There is no alternatives field. |
| `smelting` | `{ingredients, layout, output_count, station}` | `ingredients: array<string>`; `layout: null`; `output_count: integer`; `station: string` (always `furnace`). The product is `subject_item`. |
| `drop` | `{drops, silk_touch}` | `drops: array<string>` of canonical IDs, repeated for quantity; `silk_touch: boolean`. The broken block is `subject_item`. |
| `craftability` | `{ingredients, layout, output_count, station}`, `{}`, `{ingredients, station}`, `{ingredients}`, `{craftable}`, or `{station}` | `ingredients: array<string>`; `layout: array<string|null> | null`; `output_count: integer | null`; `station: string | null` (observed `anvil`, `crafting_table`, `inventory`, `smithing_table`); `craftable: boolean` (observed `false`). Fields may be absent. Ingredients normally use canonical IDs, but the generic ID `planks` remains in some historical rows. |
| `id` | `{old}`, `{old_id}`, `{identifier}`, `{new, old}`, `{new_id, old_id}`, `{old_name}`, `{new_name, old_name}`, `{new_identifier, old_identifier}`, `{old_identifier}`, or `{}` | `old`, `identifier`, `old_name`, `new_identifier`, and `old_identifier` are strings when present; `old_id` is string or integer; `new`, `new_id`, and `new_name` are string or null. These are raw numeral/identifier/name literals, not canonical item IDs. |
| `name` | `{new_name, old_name}`, `{new, old}`, `{old_name}`, `{new_name}`, or `{new}` | Each value is a display-name string or null where observed. These values preserve capitalization and spacing and are not item IDs; the renamed item itself is the canonical `subject_item`. |

LLM `recipe`/`smelting` alternatives are not represented separately: extraction
chooses one ID per ingredient/slot. Quantities use repeated IDs plus `output_count`, as
above. The audit does not use `craftability`, `id`, or `name` payloads for plan closure.

### Three real LLM examples

```json
{"claim_id":"claim_37c0b4f6eb91f233d93b","claim_type":"recipe","subject_item":"bucket","payload":{"ingredients":["iron_ingot","iron_ingot","iron_ingot"],"layout":null,"output_count":1,"station":"crafting_grid"},"span_text":"3 = bucket","extractor_version":"96b07e80"}
```

```json
{"claim_id":"claim_fa4f05827919dadb8201","claim_type":"drop","subject_item":"diamond_ore","payload":{"drops":["diamond"],"silk_touch":false},"span_text":"An ore drops a single diamond.","extractor_version":"96b07e80"}
```

```json
{"claim_id":"claim_63563cdcad267820aa9e","claim_type":"name","subject_item":"cauldron","payload":{"new_name":"Water cauldron","old_name":"Cauldron"},"span_text":"Water cauldron.","extractor_version":"96b07e80"}
```

## Extractor comparison

| Dimension | `template` | `deterministic_prose` | `llm` |
|---|---|---|---|
| Route marker | `payload.extractor=template` | `payload.extractor=deterministic_prose` | `extractor_version=96b07e80`; no payload marker |
| Frozen requested claim types | `recipe`, `smelting` | `drop` | all six requested types |
| Item representation | Canonical IDs | Canonical IDs | Canonical IDs for mechanism types; display/raw literals for `id` and `name` values |
| Product representation | `subject_item` plus `output_count` | `drops` array | Same as template for recipes/smelting; `drops` for drops |
| Quantity | Repeated input IDs and integer output count | Repeated drop IDs | Same mechanism convention; metadata types may omit fields |
| Shape | Nine-slot `layout` or null; smelting has no layout key | Not applicable | Nine-slot `layout` or null; smelting layout is null |
| Alternatives/families | Per-slot `layout_options`; concrete normalized children | Subjects expanded before storage | No alternatives field; selected IDs, with a few generic craftability rows |
| Determinism marker | `deterministic=true` | Route itself is deterministic; no separate boolean | None |
| Source evidence | Exact raw template in `span_text` | Exact prose sentence in `span_text` | Exact extracted substring in `span_text` |

Types not listed in the requested six also exist (`tool_requirement` under template
and `chest_loot` under LLM); they are intentionally outside this document's table.
