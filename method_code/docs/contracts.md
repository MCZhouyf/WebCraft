# ARBITER Contracts

## Semantic API

The runtime agent receives exactly `(goal, kit, api)`. The API exposes eight
methods and returns only public, redacted values.

| Method | Input | Output | Primary cost |
|---|---|---|---:|
| `mine` | `node_type: str` | public success/error and item flow | 1 |
| `craft_grid` | exactly nine item IDs or nulls | public success/error and item flow | 1 |
| `smelt` | input ID, fuel ID, positive `n` | public success/error and item flow | requested `n`; invalid `n` costs 1 |
| `check_name` | item identifier | public existence/name result | 1 |
| `inventory` | none | item/count list | 0 |
| `report_version` | hash of inferred time anchor | acknowledgement | 0 |
| `submit` | public result object | acknowledgement | 0 |
| `search` | text query and `k` | six-field SearchBackend rows | 0 |

Failed charged actions retain their charge. The cost contract is identical to
`webcraft/config/cost_model.json`. Coordinates, protocol details, registry
contents, hidden version strings, and other private state are excluded.

## Mechanism identity

Assertions use the exact WebCraft identity rule:

```text
canonical = JSON(assertion, sorted keys, separators=(",", ":"))
id = "cf_" + SHA256(UTF8(canonical))[:16]
```

A `ProbeSpec` contains the public method/parameters plus the canonical
mechanism and its ID. This makes assertion -> probe -> assertion round trips
lossless even when a physical probe cannot reveal every tag alternative in a
single action. Physical observations contribute only a Boolean outcome for the
mechanism; they do not disclose the private truth vector.

## Claim schema

Every statement loaded from `corpus.claims_cache.ClaimsCache(index_dir).get(chunk_id)`
contains exactly `claim_id`, `chunk_id`, `claim_type`, `subject_item`, `payload`,
`polarity`, `tense`, `span_text`, and `extractor_version`. Truth vectors,
synthetic/provisional labels, condition/index names, file paths, and other truth
annotations are forbidden recursively. Source and timestamp remain those of the
six-field SearchBackend row identified by `chunk_id`; they are not duplicated in
the claim. Historical-tense statements are represented independently from a
source's current-tense statements. `payload` is normalized into an assertion
before mechanism inference.

## Boundary

Truth files, conflict truth vectors, task feasibility, oracle costs/plans,
splits, and the set or count of candidate worlds are forbidden at runtime.
Only offline tests and evaluation may use them. Runtime code lives under
`src/arbiter/agent/` and `src/arbiter/adapters/`; both are statically scanned
on every test run. The adapter imports only WebCraft's eight-method simulator
surface, `corpus.search_backend.SearchBackend`, and
`corpus.claims_cache.ClaimsCache` after strict pin validation.
Each search row contains exactly `text`, `source_site`, `page_title`, `revid`,
`timestamp`, and `chunk_id`.
