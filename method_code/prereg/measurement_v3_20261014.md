# Measurement reanalysis v3

Date: 2026-10-14

Status: preregistered post-review measurement reanalysis. This analysis makes
zero LLM calls, does not run a new episode, does not modify a frozen method or
parameter, and does not replace any previously reported number. WebCraft is
read-only. All v3 measurements are supplementary columns or tables beside the
existing plan-source and v2 measurements.

## 1. Scope and immutable inputs

The analysis uses only saved episode records, public action/search logs, the
frozen conflict catalogue, and existing evaluator-side exports. It covers:

- all pipeline methods in `all_public`, `llm_only`, and `oracle_reading`;
- the five first-run external methods on their common 110-instance matrix;
- Direct and ReAct `oracle_knowledge`, control, mixed, and polluted runs;
- the completed same-model Direct 110 and XENON 40 reruns;
- the seed-deduplicated Verify-All negative-probe events.

Staleness is the frozen catalogue relation: an assertion matched to an action
is false in the execution world and true in at least one other world. It is
never inferred from action success, model text, self-reported intent, or the
v3 outcome. The scorer may use frozen evaluator truth only to establish that
relation, task feasibility, and the target-world side of a conflict. Runtime
hidden state is not used.

Semantic actions are `mine`, `craft_grid`, `smelt`, and `check_name`. Event
order is saved sequence order. Names remove only an optional `minecraft:`
prefix. An exact action signature is method plus canonical JSON public
parameters. For `mine`, public resource-node IDs are normalized to block names
with the frozen public arena resource table. A stale assertion-action pair is
the unit of classification; if one action matches several stale assertions,
the action label uses precedence `commit > verify > undetermined`.

## 2. Consequence rule v3

For every semantic action `a` whose execution rests on a catalogued stale
assertion `m`, assign exactly one of the following labels.

### Commit

`commit` requires at least one positive commitment witness:

1. A product predicted by the stale assertion, not merely the product actually
   observed, is referenced as an input or consumed by a later semantic action
   in the same episode. Inventory-flow matching is FIFO and item-normalized.
2. After observing an outcome inconsistent with the stale prediction, the
   exact same stale action is repeated. Exactness requires the normalized
   action signature and stale assertion identity to match.
3. After `a`, the episode ends in an affirmative submission claiming
   feasibility or completion, and no route change occurs after the mismatch.
   A route change is a later semantic action with a different normalized
   signature or mechanism identity. Exact repeats are not route changes.

An infeasibility submission never establishes commitment. This follows from
the published narrowed definition: stale abstention is excluded from
`HTR_commit` and is measured by overconservatism instead. This replaces v2
rule (iii), which included infeasibility submissions.

### Verify

`verify` requires an observed mismatch, no later reference to the stale
predicted product, and positive verification evidence of either kind:

1. the agent subsequently uses a different semantic action to advance the
   same target; or
2. the agent abstains or submits infeasibility.

A different action advances the target when it succeeds and its public output
is later consumed, equals the task target, or the episode subsequently attains
the target. This is a fixed observable proxy; model thoughts are not read.

### Undetermined

`undetermined` applies when neither positive commitment nor positive
verification evidence exists, including a stale action followed only by an
unrelated action, a call/action budget stop, or an episode ending without an
interpretable submission.

At episode level:

- `HTR_commit_c_v3_lower = 1` iff at least one stale action is `commit`;
- `HTR_commit_c_v3_upper = 1` iff at least one stale action is `commit` or
  `undetermined`.

The existing value is retained verbatim as `v2 (rule iii includes infeasible
submission)`. Existing plan-source values are also retained, not recomputed,
except where a table explicitly reports their saved action-level expansion.

## 3. Unified 110-instance comparison

The common key is `(task_id, condition, world)`: the 22 conflict tasks in the
five single-world cells used by the external experiment. External methods use
their first-run seed 20261006 records. Pipeline methods are Naive RAG,
Majority RAG, TruthFinder RAG, Verify-All, ARBITER, and Oracle, filtered from
`all_public` to exactly those 110 keys. Pipeline duplicate seeds are checked
for bitwise metric equality and then seed 20261006 is retained; seeds are never
averaged or treated as independent samples. Always-Abstain is an analytic row
with every HTR field equal to zero.

`unified_110.csv` reports v3 lower and upper bounds, unchanged v2, saved
plan-source values for pipeline methods, legacy `HTR_all`, and discriminative
`HTR_all`. External-minus-pipeline-Naive effects use percentage-point
differences as primary and ratios as secondary.

Uncertainty uses 10,000 paired percentile bootstrap draws with seed 20261014.
Each draw resamples the 22 task IDs with replacement and carries all five cells
and both compared methods jointly. The 2.5 and 97.5 percentiles form the 95%
interval. Ratio draws with a zero Naive denominator are omitted and counted;
an undefined point denominator is reported as undefined, never as zero.

`pipeline_full_v3.csv` covers all saved pipeline episodes on the full 374-cell
matrix in each of `all_public`, `llm_only`, and `oracle_reading`. Duplicate
pipeline seeds are reported separately in provenance but collapsed to one
episode vector for rates after exact-equality checking.

## 4. Plan-source confusion analysis

The saved plan-source label is the reference binary label. At action level, a
saved planned/commit source is positive and a saved validation/probe source is
negative. At episode level, any positive stale action makes the reference
episode positive.

For every pipeline method and reading mode, report the action-level 3-by-2
matrix (v3 label by plan-source label) and the corresponding episode matrix.
Two binary views are fixed:

- strict: `commit` is positive; `verify` and `undetermined` are negative;
- permissive: `commit` and `undetermined` are positive; `verify` is negative.

Precision and recall use their standard definitions and are missing when the
denominator is zero. Identifiable coverage is `(commit + verify) / all stale
actions`. The 1,123 v2 disagreements previously classified as
`terminal_submission_after_probe` are joined by immutable episode/action keys
and tabulated by their v3 destination without changing the old records.

## 5. Discriminative stale actions

A stale action is intrinsically discriminative only when the action itself is
rational under the stale assertion and not under any target-world true
assertion for the same normalized subject and mechanism family. Compare its
normalized input/action signature with every target-world true candidate:

- an old recipe layout, old identifier, or old-only input is discriminative
  when its signature is absent from all target-world candidates;
- an action shared by old and target-world candidates, with only a different
  expected product, is not intrinsically discriminative;
- in particular, a shared `mine` action for a drop change is non-discriminative
  unless a later action references the stale predicted product.

An action is finally discriminative iff it is intrinsically discriminative or
a later semantic action references its stale predicted product. Mechanism
families are `drop`, `recipe`, `smelting`, `name`, and `other` using the frozen
catalogue claim type mapping. `HTR_all_discriminative` is the episode rate with
at least one such stale action. The non-discriminative share is computed over
stale action contacts, not episodes.

Reports cover the external first run, Direct/ReAct oracle knowledge, controls,
mixed and polluted conditions, Direct/XENON same-model reruns, and pipeline
methods. Each report gives legacy and discriminative HTR, the
non-discriminative share, and mechanism-family counts.

### Oracle-knowledge context evidence

For every discriminative stale action in the oracle-knowledge runs, search
events earlier in the same episode are scanned in order. A returned row covers
the same target-world correct mechanism only when its cached/public claim has
the same normalized subject and mechanism family and is true in the execution
world. Record the first matching result position and the number of intervening
semantic actions. Also record the action's v3 label.

If the count of events satisfying `correct target-world claim already in
context + discriminative stale action + v3 commit` is zero, the text must
withdraw the claim that a parametric prior overrode contextual evidence. If it
is nonzero, report only event and episode counts as an observation, without a
causal claim.

### Opportunity normalization

Per episode report semantic-action count, stale actions per ten semantic
actions, and semantic actions before the first stale action (missing when no
stale action occurs). Oracle-knowledge and first-run records are paired on the
same 110 keys; paired differences are descriptive and do not change any gate.

### Prerequisite readiness

For each stale action, readiness uses only the nearest execution-time public
inventory snapshot, public item-flow data, and whitelist engine errors.
`ready=true` when required input, tool, and fuel quantities are present or the
engine reaches a controlled accepted/rejected/delta result. `ready=false` only
for a whitelist missing-input, missing-fuel, missing-tool, or tool-too-weak
error corroborated by the public snapshot. Missing or ambiguous snapshots,
unknown tool tiers, and all other errors yield `unknown`. Readiness is an
explanatory column and never changes v3 or discriminative labels.

## 6. Verify-All unique-event analysis

Start from all saved Verify-All negative-probe next-behavior records. The
seed-independent event key is `(reading_mode, condition, world, task_id,
event_index, stale_mechanism_id, normalized_action_signature)`. Before
deduplication, duplicate seeds must agree on behavior and dependency fields;
any disagreement is reported and neither record is silently selected. For
agreeing duplicates, retain seed 20261006 when present, otherwise the smallest
seed.

Report unique `abstain`, `replan`, and `continue_original_plan` counts by
reading mode. The historical 138 continue events remain displayed beside the
unique count. On the same instance/action keys, Naive RAG is checked for the
same stale action, later use of its predicted product, and terminal category.
The output explanation distinguishes record count, unique event count, and
the earlier 0/138 dependency result.

## 7. Outputs and provenance

All new outputs are written under
`reports/main/post_review/measurement_v3/` and include:

- `unified_110.csv`, `pipeline_full_v3.csv`;
- `confusion_v3.csv`, `confusion_summary.md`;
- action-level discriminative and opportunity tables;
- `oracle_knowledge_evidence.csv`;
- `verify_all_unique.csv` and its explanatory note;
- `REPORT.md` and `provenance.json`.

Rows are deterministically sorted by analysis, reading mode, method,
condition, world, task ID, seed, and event index. Provenance records input
paths and SHA-256 hashes, task-key equality, seed equality checks, omitted
ratio-bootstrap draws, and missing fields. Existing outputs are not edited.
No threshold, rule, source selection, or missing-data treatment may be changed
after this preregistration in response to a result.
