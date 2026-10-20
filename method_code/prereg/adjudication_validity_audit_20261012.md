# Adjudication validity audit, 2026-10-12

This is a zero-LLM, read-only audit of existing execution logs. It does not
change a frozen method, parameter, prompt, result, or WebCraft file, and it
does not run a new episode. Classification uses only action parameters,
execution-time observable inventory, public item deltas/results, and the
semantic interface's public error vocabulary. It does not load task truth,
oracle plans, conflicts, recipes, source specifications, or any other hidden
label.

## Frozen inputs and scope

The audit reads the following episode indexes and the action logs referenced
by them. Their SHA-256 digests at registration are:

| Input | SHA-256 |
|---|---|
| `artifacts/post_review/external_agents/final/direct_llm_assembled/episodes.jsonl` | `5c1901cb3da980ee3f6b89cabdd106e3c67573e3d93d81820918e4ca4af95c29` |
| `artifacts/post_review/external_agents/final/react_assembled/episodes.jsonl` | `b57d11ef341c832470e3fa37995290a3db45c1c125c5d7143bc6670d57b09c11` |
| `artifacts/post_review/external_agents/final/reflexion_assembled/episodes.jsonl` | `af894b0a1917f1813d26cde830435f5efa5b3984fae3827ebe9dc37e69e9e447` |
| `artifacts/post_review/external_agents/final/xenon_assembled/episodes.jsonl` | `ef8eea2f300da88f4534004d3192d52fd93d5b2978f4d8cc4f1e5dae7e997332` |
| `artifacts/post_review/external_agents/final/adam/episodes.jsonl` | `69a82f35a2c49bd5b4b6df6a045e98447fe39f1be7ba3d2b2648b7c7d6d89c59` |
| `artifacts/main/all_public/episodes.jsonl` | `a73a99871810cf416ebbb33588cca3185aa6621ae848b45c2d212ce1c9107134` |
| `artifacts/main/llm_only/episodes.jsonl` | `b0e87c93a7f8b25d15db965e7f406b416c6e0910b3b794aae96c1c69d36af4a1` |
| `artifacts/post_run_probe_then_rag/all_public/episodes.jsonl` | `32555ecac0ca92147dbcc1055b936bb1a82d63fecb8f798686ecea5fcc969fc7` |
| `artifacts/post_run_probe_then_rag/llm_only/episodes.jsonl` | `ec09d6d9d835e824bad4fa8dbef4f5c0cb93a636ae8fda5c0c85384c772a1652` |

The pinned public boundary is WebCraft commit
`166d48a317fc3a8de72a5329ed45412826c29ad2`; the pin file SHA-256 is
`697b81a8d33b27ecef36cffcff082a9ea20eed0548799306a8cd98741199389d`.
The already pinned semantic interface source has SHA-256
`f9c870e59d52dd5e7af190d476375699afcca41499b4a80f9687ea631588f3cc`.
WebCraft remains read-only.

Audited records are:

1. Every failed `mine`, `craft_grid`, `smelt`, or `check_name` action in the
   110 first-round episodes of each of Direct LLM, ReAct, Reflexion, XENON,
   and ADAM.
2. Separately, all ADAM actions with declared `intent=probe`, semantic method,
   and positive recorded primary cost. The expected count is 65; overlap with
   the failed-action cohort is retained in both named scopes, not silently
   deduplicated across questions.
3. Every declared semantic probe of Verify-All in both `all_public` and
   `llm_only` main logs.
4. Every original-ARBITER retirement in both modes. A retirement is frozen as
   a `policy_trace` record with `intent=probe` and `observed=false`, joined by
   semantic-action ordinal to its execution event. All seeds remain records.
5. Every declared semantic Probe-then-RAG version probe in both modes as the
   positive control.

The previous `40/52` audit covered only negative ARBITER probes in
`all_public` whose mechanism ID was in that episode's conflict-mechanism list,
after collapsing deterministic seed duplicates by
`(reading_mode, condition, world, task_id, mechanism_id, action_signature)`.
This audit reports both the full raw retirement count and seed-collapsed count,
plus the previous subset's raw and seed-collapsed coverage. The expected old
subset is 104 raw records and 52 collapsed records; a mismatch stops the audit.

## Observable inventory reconstruction

For each episode, an `inventory` result is an observable anchor. Inventory
immediately before every action is reconstructed forward and backward from
the nearest anchor using only each logged `item_flow.consumed` and
`item_flow.produced`. Independent anchors must agree with reconstruction; an
inconsistency makes the affected action `indeterminate`. No task initial
inventory or final hidden state is loaded.

Readiness is fixed as follows:

- `craft_grid`: every non-null layout item must be present in the required
  multiplicity.
- `smelt`: the requested input must be present at least `n` times and the
  requested fuel must be present. For the public frozen coal interface, fuel
  need is `ceil(n/8)`; any other requested fuel is present-ready at one unit
  but cannot establish semantic validity from inventory alone.
- `check_name`: has no inventory prerequisite.
- `mine`: tool rank is
  `wooden/golden=0, stone=1, iron=2, diamond=3, netherite=4`. Public node-ID
  families require rank 1 for `iron`, `gold`, and `lapis`, rank 2 for
  `diamond` and `redstone`, rank 0 for `stone`, `cobblestone`, and `coal`, and
  no tool for `log`, `sand`, and `gravel`. An absent or lower-rank held tool is
  `missing_tool`. This fixed interface-precondition table is embedded in the
  audit; no truth/source file is consulted at runtime.

## Frozen five-way classifier

The expected outcome for a pipeline action is the third element of its saved
`action_signature`; the trace and semantic actions are joined by ordinal. For
external actions and Probe-then-RAG probes without a saved expected claim, a
successful public result is valid confirmation of the requested operation,
but no unlogged expected product is imputed.

Classification precedence is:

1. `indeterminate`: no consistent observable inventory snapshot, malformed
   event/signature, unrecognized public error, or a generic mining rejection
   for which the fixed table and held tools cannot establish readiness.
2. `uncontrolled`: the action failed while an inventory prerequisite was
   absent. Subreason is `missing_tool`, `missing_input`, or `missing_fuel`;
   multiple shortages are recorded but count once.
3. `controlled_confirmed`: prerequisites were ready, execution succeeded, and
   the public item delta contains the saved promised output/count; when no
   expected claim was logged, a successful, internally consistent public
   result is confirmation of the requested operation.
4. `controlled_with_delta`: prerequisites were ready and execution succeeded,
   but its public item delta differs from a saved promised output/count.
5. `controlled_rejected`: prerequisites were ready and the public engine
   rejected the requested operation. This includes `product=null`, unknown or
   depleted public resource node, invalid recipe/name, and smelting timeout.

The frozen public error whitelist is: `unknown resource node`, `resource node
is empty`, `layout must contain exactly 9 cells`, `crafting table unavailable`,
`furnace unavailable`, `missing smelting input or fuel`, `smelting timeout`,
`n must be a positive integer`, `episode closed`, `invalid item identifier`,
`missing ingredient`, and `action could not be completed`. A generic
`action could not be completed` for `mine` is `uncontrolled/missing_tool` only
when the fixed node/tool table proves the held tier insufficient; otherwise it
is `indeterminate`. The same generic error outside mining is `indeterminate`.

Mechanism families are `ore_drop` for `mine`, `recipe` for `craft_grid`,
`smelting` for `smelt`, `name` for `check_name`, and `other` otherwise.
"Outside ore" means the union of `recipe`, `smelting`, `name`, and `other`.

## Aggregation and gate G1

`per_method.csv` has one row per named scope, reading mode, and method, with
the five counts and proportions. `per_mechanism.csv` uses the same scopes and
five categories by mechanism family. `action_audit.csv` preserves the
record-level classification and observable evidence. Proportions use all
audited records in that row as denominator; empty rows are reported as NA.

The external range in the report is the minimum and maximum of the five
methods' uncontrolled proportions among failed semantic actions, not a
confidence interval. Verify-All and ARBITER are reported separately by mode
and pooled across modes. The outside-ore proportion is uncontrolled divided
by all audited outside-ore records, with per-family values also exported.

G1 uses the author thresholds without tuning:

1. `general phenomenon` if at least three external methods have uncontrolled
   proportion >= 0.50 and at least one outside-ore family has uncontrolled
   proportion >= 0.30.
2. Otherwise `mechanism-local phenomenon` if there is at least one
   uncontrolled ore-drop record and every nonempty outside-ore family is
   below 0.30.
3. Otherwise `implementation-local problem` if at least three of the five
   external methods are below 0.30.
4. Otherwise `inconclusive` because the stated thresholds do not exhaust the
   mathematical outcome space.

The report contains three numbered adjudication sentences: the external
five-method range; Verify-All and original-ARBITER retirement proportions;
and the outside-ore proportion with G1. The script prints and reports actual
results without reinterpretation or threshold changes.

No audit computation or output existed when this registration was committed.
