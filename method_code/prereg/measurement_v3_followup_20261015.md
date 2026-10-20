# Measurement-v3 follow-up A4

Date: 2026-10-15

Status: preregistered zero-call follow-up. This analysis reads only completed
logs and existing exports. It runs no episode, makes no LLM call, does not
modify frozen methods or previously reported numbers, and treats WebCraft as
read-only. New measurements are parallel audit columns and tables.

## Fixed inputs and seed handling

Pipeline inputs are the completed `all_public`, `llm_only`, and
`oracle_reading` episode files already used by measurement v3, including the
post-run Probe-then-RAG files. External inputs are the five first-run methods
on their common 110-instance matrix. A Qwen row is included only if a completed
assembled `second_family_qwen` episode export exists before this commit;
otherwise its absence is reported and no surrogate is constructed.

Pipeline duplicate seeds must agree on the compared public behavior fields.
After checking agreement, seed 20261006 is retained; seeds are never averaged
or treated as independent. The common pairing key is `(reading_mode,
condition, world, task_id)` for pipeline analyses and `(condition, world,
task_id)` for the external 110 matrix.

All bootstrap and paired-permutation procedures use 10,000 draws and seed
20261015. The resampling unit is task ID: all cells and both compared methods
for a sampled task travel together. Percentile 2.5/97.5 quantiles form the 95%
interval. Zero-denominator conditional-rate draws are omitted and counted.

## 1. Semantic action identity

The four ordered method pairs are Naive/Verify-All,
Parametric/Probe-then-Parametric, Naive/ARBITER-shell, and Naive/ARBITER.
Compare all 374 instances in each reading mode after seed deduplication.

Semantic actions are `mine`, `craft_grid`, `smelt`, and `check_name`. The
canonical action tuple is:

1. method name;
2. canonical JSON of public parameters, preserving list order and values;
3. public `success` plus canonical public `result` after removing only its
   duplicated nested `cost` object;
4. canonical top-level public `cost`.

`intent`, probe/commit labels, policy traces, timestamps, sequence numbers,
and model text are ignored. Two sequences are completely identical iff every
canonical tuple and sequence length match.

The first mismatch is classified in fixed priority: `left_extra_action` or
`right_extra_action` when one sequence ends after an identical prefix;
`parameter_different` when methods match but parameters differ;
`result_different` when method and parameters match but result differs;
`cost_different` when only cost differs; otherwise `order_different` when
methods differ. Report the right-minus-left semantic-action count distribution
and the first mismatch position. A Verify-All probe label is audited
separately: its action must occur at the same zero-based semantic position in
Naive with the same method and public parameters. The final sentence says
that label differences correspond to behavior differences iff at least one
Naive/Verify-All sequence is not completely identical.

## 2. Dependence after the 69 unique negative events

Use exactly the seed-deduplicated keys in `measurement_v3/verify_all_unique.csv`.
For both Verify-All and Naive, replay public inventory flow with FIFO lots.
For each negative stale action record two notions:

- `name_reference`: a later semantic action names or consumes an item predicted
  by the rejected assertion, irrespective of where that same-named item came
  from;
- `causal_lot_dependency`: the later semantic action consumes a FIFO inventory
  lot actually produced by that stale action.

The unified primary comparison is `causal_lot_dependency`. A name reference
whose consumed lot came from initial inventory or another action is reported
as `same_name_other_source`, not propagation from the stale action. Missing
Naive matching actions are explicit zeroes with status `action_absent`, not
silently dropped. The old Verify-All 0/69 is checked, not overwritten.

## 3. External stale propagation

Use the measurement-v3 action matches and v3 labels on the common 110 matrix.
A world-denied stale action is a matched stale action with `mismatch=1`.
Conditional propagation is:

`number of world-denied stale actions labelled commit / number of
world-denied stale actions`.

Commit triggers are counted non-exclusively because one event may have several
positive witnesses: (i) predicted product referenced/consumed, (ii) exact
stale action repeated after mismatch, and (iii') affirmative submit without a
route change. For each rule, choose the first three events in deterministic
method/condition/world/task/event order (or all when fewer than three) and
export the stale action, public result, and dependent/repeated/submit action.

Report episode lower/upper bounds beside the conditional rate. The condition
split is matched three cells versus mismatched two cells. Differences are
`mismatched - matched`; both task groups retain all their registered cells in
each bootstrap draw.

Interface format validity is evaluated only from the public call shape:
`mine` requires a nonempty string `nodeId`; `craft_grid` a nine-element list
of strings/null; `smelt` nonempty string `input` and, when present, string
`fuel`; `check_name` nonempty string `name`; affirmative `submit` requires a
dictionary `result`. For rule (i) the first downstream referencing action is
checked, for rule (ii) the first exact repeat, and for rule (iii') the
affirmative submit. A commit is retained after format exclusion iff at least
one of its trigger witnesses points to a legal-format downstream action.

## 4. Behavior-only distinguishability

Scope A contains the ten completed pipeline methods plus analytic
Always-Abstain, across each of the three reading modes and 374 seed-deduplicated
instances. Scope B contains the five external methods, Naive, Verify-All,
ARBITER, Always-Abstain, and Oracle on the identical 110 keys.

The six mutually exclusive terminals, in priority order, are `solved`,
`correct_infeasible`, `false_infeasible`, `budget_exhausted`,
`execution_failed`, and `plan_not_found`. Always-Abstain is
`correct_infeasible` for infeasible tasks and `false_infeasible` otherwise;
its SR is one only for infeasible tasks and all action/HTR fields are zero.

An SR pair is `indistinguishable` when its task-paired bootstrap difference
interval includes zero. Among those pairs, the behavior-only audit uses:

- discriminative HTR_all;
- v3 lower and upper consequence HTR;
- the six-way terminal distribution;
- semantic actions per episode.

A scalar rate separates a pair only when its 95% paired interval excludes zero
and its absolute point difference is at least 0.15. Mean semantic-action count
uses the same interval rule and a 0.15 action/episode threshold. Terminal
distributions use task-paired label swapping, two-sided `p < 0.05`, and total
variation distance at least 0.15. No multiplicity adjustment is added because
the requested v2 comparison uses the registered per-pair inference rule.

The parallel v2 column uses the same inference machinery and thresholds, but
its secondary set is legacy HTR_all, accepted plan-source/self-reported
HTR_commit, probe count, probe cost, terminal distribution, and semantic-action
count. The behavior-only result never uses plan-source HTR or probe labels.
A pair is separated when any eligible secondary metric passes its fixed rule.
Report total pairs, SR-indistinguishable pairs, behavior-separated pairs among
them, and the unique method-pair names for both A4 and v2.

## Outputs

All new files go under `reports/main/post_review/measurement_v3_followup/`:

- `action_identity.csv`, `action_identity_mismatches.csv`, and
  `verify_all_probe_position.csv`;
- `negative_dependency.csv` and `negative_dependency_summary.csv`;
- `external_propagation.csv`, `external_propagation_conditions.csv`,
  `external_propagation_witnesses.csv`, and bootstrap tables;
- `behavior_distinguishability.csv`, its summary, and `REPORT.md`;
- `provenance.json` with SHA-256 inputs and zero-call declaration.

Rows are deterministically sorted. Missing data, undefined ratios, and absent
Qwen exports are explicit. No rule, threshold, source, or missing-data policy
may change after this preregistration in response to a result.
